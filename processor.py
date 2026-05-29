# Author: Administrator
# Created: 2026-05-25
"""Processing pipeline for 耀我科技上传器 v2.0 — batch scheduler, Phase 1/2, Excel write."""

import os
import re
import time
import threading
from openpyxl import load_workbook

from models import Product, Batch, ProductStatus
from config_manager import load_config, load_prompts, load_categories, load_banned_words
from config_manager import load_category_zh, save_category_zh
from api_client import deepseek_chat, generate_image, haomingai_identify, download_image
from api_client import create_storage_provider

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- Helpers ----

def _color_to_korean(color, translate_fn):
    """Translate color name to Korean if needed."""
    if not color:
        return color
    if re.match(r'^[가-힣]+$', color):
        return color
    try:
        return translate_fn(color, target="ko")
    except:
        return color


def _strip_particles(word):
    """Strip Korean particles from a keyword."""
    particles = ['으로', '로', '의', '은', '는', '이', '가', '을', '를',
                 '에', '에게', '도', '만', '와', '과', '하고', '이나', '나']
    for p in particles:
        if word.endswith(p) and len(word) > len(p) + 1:
            return word[:-len(p)]
    return word


def _url_stem(url):
    """Strip _thumbnail suffix from URL for dedup."""
    if not url:
        return url
    return re.sub(r'_thumbnail[^/]*(\.[\w]+)$', r'\1', url)


# ---- Excel Read/Write ----

def load_products(source_path):
    """Load products from scraped Excel, grouping by ParentSKU."""
    wb = load_workbook(source_path, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(min_row=2, values_only=True)  # generator, not list
    groups = {}
    for row in rows:
        if not row[0]:
            continue
        psku = str(row[0])
        if psku not in groups:
            groups[psku] = Product(
                parent_sku=psku,
                title=str(row[2] or ""),
                tag=str(row[4] or ""),
                price=str(row[12] or ""),
                url=str(row[11] or ""),
                main_img=str(row[17] or ""),
            )
        color = str(row[9] or "")
        size = str(row[10] or "")
        price = str(row[12] or "")
        if color and color != "Default" and color not in groups[psku].colors:
            groups[psku].colors.append(color)
        if size and size != "One Size" and size not in groups[psku].sizes:
            groups[psku].sizes.append(size)
        groups[psku].color_sizes.append((color, size, price))
        vi = str(row[40] or "")
        if vi and vi not in groups[psku].variant_imgs:
            groups[psku].variant_imgs.append(vi)
        for i in range(17, 38):
            u = str(row[i] or "")
            if u and u.startswith("http") and u not in groups[psku].extra_imgs:
                groups[psku].extra_imgs.append(u)
    return list(groups.values())


def detect_completed(output_path):
    """Return set of ParentSKUs already written to output Excel."""
    if not output_path or not os.path.exists(output_path):
        return set()
    try:
        wb = load_workbook(output_path, read_only=True)
        ws = wb["NEW 일반상품"]
        done = set()
        for row in ws.iter_rows(min_row=8, values_only=True):
            # Column B is ParentSKU-like, but we check column E (AI title) to confirm it was processed
            title_cell = row[4] if len(row) > 4 else None
            sku_cell = row[0] if row[0] else None
            if title_cell and str(title_cell).strip():
                # Use row index as indicator; store SKU from col E or col B
                done.add(str(row[1]) if row[1] else "")
        return done
    except:
        return set()


def init_output_workbook(template_path, output_path, cfg=None):
    """Copy template to output. Return (workbook, sheet, start_row, fixed_dict)."""
    if cfg is None:
        cfg = load_config()
    twb = load_workbook(template_path)
    tws = twb["NEW 일반상품"]
    tpl_row = 8
    # Read fixed values
    _tpl = {c: tws.cell(row=tpl_row, column=c).value for c in range(1, 67)}
    fixed = {
        "B": _tpl.get(2, "옥션/G마켓"),
        "C": _tpl.get(3, "ruijiaju11"),
        "D": _tpl.get(4, "ruijiaju11"),
        "N": _tpl.get(14, 90),
        "U": cfg.get("default_quantity", 50),
        "V": cfg.get("default_quantity", 50),
        "AD": _tpl.get(30, "일반택배"),
        "AE": _tpl.get(31, 23873963),
        "AF": _tpl.get(32, 109827381),
        "AG": _tpl.get(33, 47033613),
        "AH": _tpl.get(34, -202),
        "AI": _tpl.get(35, -202),
        "AJ": _tpl.get(36, 10013),
        "AK": _tpl.get(37, 0),
        "AL": _tpl.get(38, 2),
        "AM": _tpl.get(39, 235839),
        "BB": _tpl.get(53, "해당없음"),
        "BC": _tpl.get(54, "해외수입"),
        "BE": _tpl.get(56, "단일원산지"),
        "BK": _tpl.get(63, "과세상품"),
    }
    return twb, tws, fixed


def write_product_row(tws, row_idx, prod, fixed, tpl_start_row):
    """Write one product row into the template sheet."""
    r = tpl_start_row + row_idx
    # A: seq
    tws.cell(row=r, column=1, value=r - 5)
    # B/C/D: fixed
    tws.cell(row=r, column=2, value=fixed["B"])
    tws.cell(row=r, column=3, value=fixed["C"])
    tws.cell(row=r, column=4, value=fixed["D"])
    # E: AI title
    tws.cell(row=r, column=5, value=prod.ai_title)
    # K/L/M: category
    tws.cell(row=r, column=11, value=prod.result.get("K", ""))
    tws.cell(row=r, column=12, value=prod.result.get("L", ""))
    tws.cell(row=r, column=13, value=prod.result.get("M", ""))
    # N: fixed
    tws.cell(row=r, column=14, value=fixed["N"])
    # O/P: price
    p = prod.result.get("O", prod.price)
    tws.cell(row=r, column=15, value=p)
    tws.cell(row=r, column=16, value=p)
    # U/V: fixed
    tws.cell(row=r, column=21, value=fixed["U"])
    tws.cell(row=r, column=22, value=fixed["V"])
    # W/X/Y: type/attr/color_size
    tws.cell(row=r, column=23, value=prod.result.get("W", "미사용"))
    tws.cell(row=r, column=24, value=prod.result.get("X", "색상"))
    tws.cell(row=r, column=25, value=prod.result.get("Y", ""))
    # Z: main image URL
    tws.cell(row=r, column=26, value=prod.result.get("Z", prod.main_img))
    # AA: variant images
    tws.cell(row=r, column=27, value=prod.result.get("AA", ""))
    # AB: detail HTML
    tws.cell(row=r, column=28, value=prod.result.get("AB", ""))
    # AD~AM: fixed
    tws.cell(row=r, column=30, value=fixed["AD"])
    tws.cell(row=r, column=31, value=fixed["AE"])
    tws.cell(row=r, column=32, value=fixed["AF"])
    tws.cell(row=r, column=33, value=fixed["AG"])
    tws.cell(row=r, column=34, value=fixed["AH"])
    tws.cell(row=r, column=35, value=fixed["AI"])
    tws.cell(row=r, column=36, value=fixed["AJ"])
    tws.cell(row=r, column=37, value=fixed["AK"])
    tws.cell(row=r, column=38, value=fixed["AL"])
    tws.cell(row=r, column=39, value=fixed["AM"])
    # BB/BC/BE/BK: fixed
    tws.cell(row=r, column=53, value=fixed["BB"])
    tws.cell(row=r, column=54, value=fixed["BC"])
    tws.cell(row=r, column=56, value=fixed["BE"])
    tws.cell(row=r, column=63, value=fixed["BK"])


# ---- Phase 1: Serial per-product ----

def phase1_title(prod, banned_words, prompts):
    """Generate Korean title via DeepSeek."""
    is_multi = len(prod.colors) > 1
    color_note = ""
    if is_multi:
        color_note = "CRITICAL: This product has MULTIPLE colors. Do NOT mention any color in the title."
    else:
        color_note = "You may include the color keyword in the title."

    title_tpl = prompts.get("title", "")
    if "{product_title}" in title_tpl:
        prompt = title_tpl.format(color_note=color_note, product_title=prod.title,
                                  banned_words=", ".join(banned_words))
    else:
        prompt = f"""你是韩国电商标题优化专家。生成Gmarket商品标题：
- CRITICAL: 纯韩文标题必须45字符以内
- 极致本土化用语，韩国消费者使用的自然表达
- 包含高流量关键词
- 禁止：品牌名、年/月/日
- {color_note}
- 只输出标题文本本身，不要任何解释。

产品：{prod.title}
违禁词：{', '.join(banned_words)}"""
    result = deepseek_chat(prompt, max_tokens=150, temp=0.7)
    title = result.strip()
    # Hard truncate to 45 characters — Gmarket strict limit
    if len(title) > 45:
        title = title[:45]
    return title


# ---- Profile key to Korean translation (DeepSeek + JSON cache) ----
import os as _os, json as _json

def _load_profile_ko():
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "profile_ko.json")
    if _os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return _json.load(f)
        except:
            pass
    return {}

def _save_profile_ko(cache):
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "profile_ko.json")
    with open(p, 'w', encoding='utf-8') as f:
        _json.dump(cache, f, ensure_ascii=False, indent=2)

def _profile_to_korean(profile_key):
    """Translate a profile key (any language) to Korean via DeepSeek, with JSON cache."""
    cache = _load_profile_ko()
    if profile_key in cache:
        return cache[profile_key]
    try:
        prompt = "将以下电商品类关键词翻译为韩文。只输出一个韩文单词，不要解释，不要标点。\n关键词：" + profile_key + "\n韩文："
        ko = deepseek_chat(prompt, max_tokens=50, temp=0.3).strip()
        if ko and len(ko) > 1:
            cache[profile_key] = ko
            _save_profile_ko(cache)
            return ko
    except:
        pass
    return ""


def _find_category_parent(categories, profile_key):
    """Find the deepest common ancestor of all category paths matching profile_key.

    Translates profile_key to Korean via DeepSeek (cached), then searches
    the category table. Works for any language (Chinese, English, Korean).

    Steps:
      1. Translate profile_key to Korean (cached)
      2. Collect ALL paths where any segment matches the Korean term
      3. Compute the longest common prefix of all matched paths
      4. That common ancestor is the broad category scope
    """
    search_terms = []
    kr = _profile_to_korean(profile_key)
    if kr:
        search_terms.append(kr)
    if profile_key != kr:
        search_terms.append(profile_key)

    matched_paths = []
    for path in categories:
        segments = path.split(">")
        for seg in segments:
            if seg in search_terms:
                matched_paths.append(segments)
                break

    if not matched_paths:
        return set()

    min_len = min(len(p) for p in matched_paths)
    common = []
    for i in range(min_len):
        seg_i = matched_paths[0][i]
        if all(p[i] == seg_i for p in matched_paths):
            common.append(seg_i)
        else:
            break

    if not common:
        return set()

    return {">".join(common)}



def phase1_category(prod, categories, profile_key=""):
    """Match product to ESM/Gmarket category.

    Multi-source keyword voting:
      - Profile bias (x50000): user-selected prompt category -> broad scope
      - Overlap tag&title (x25000): word confirmed by both sources
      - All tag words (x20000): user-labeled product info
      - All title words (x15000): additional semantic clues
    """
    title = prod.title
    tag = prod.tag

    # Layer 1: find broad category parents from profile_key
    broad_parents = set()
    if profile_key:
        broad_parents = _find_category_parent(categories, profile_key)

    def _extract_keys(text):
        words = re.findall(r'[가-힣\w]+', text)
        keys = []
        for w in words:
            stripped = _strip_particles(w)
            if len(stripped) > 1 and stripped not in keys:
                keys.append(stripped)
        return keys

    tag_keys = _extract_keys(tag)
    title_keys = _extract_keys(title)

    # Overlap: words in BOTH tag and title -- strongest text signal
    overlap_keys = [k for k in tag_keys if k in title_keys]
    tag_only = [k for k in tag_keys if k not in overlap_keys]
    title_only = [k for k in title_keys if k not in overlap_keys and k not in tag_keys]

    scored = []
    for path, codes in categories.items():
        score = 0
        segments = path.split(">")
        last_seg = segments[-1] if segments else ""

        def _score_kw(kw, weight_exact, weight_partial, weight_upper, weight_upper_partial):
            s = 0
            if kw == last_seg:
                s += len(kw) * weight_exact
            elif kw in last_seg and len(kw) > 1:
                s += len(kw) * weight_partial
            else:
                for lv, seg in enumerate(segments[:-1]):
                    if kw == seg:
                        s += len(kw) * (lv + 1) * weight_upper
                    elif kw in seg and len(kw) > 1:
                        s += len(kw) * (lv + 1) * weight_upper_partial
            return s

        # Layer 1: broad category bias from profile (x50000)
        for pfx in broad_parents:
            if path.startswith(pfx):
                score += 50000

        # Layer 2: overlap keys (x25000)
        for kw in overlap_keys:
            score += _score_kw(kw, 25000, 12500, 50, 25)

        # Layer 3: all tag words (x20000) -- not just last word
        for kw in tag_only:
            score += _score_kw(kw, 20000, 10000, 40, 20)

        # Layer 4: all title words (x15000) -- wider semantic clues
        for kw in title_only:
            score += _score_kw(kw, 15000, 7500, 30, 15)

        if score > 0:
            scored.append((score, path, codes))

    if not scored:
        return "", "", "", ""

    scored.sort(key=lambda x: -x[0])

    # Clear winner or DeepSeek refinement
    if len(scored) > 1 and scored[0][0] > scored[1][0] * 1.5:
        best = scored[0][2]
    else:
        candidates = scored[:15]
        sample = "\n".join([p for _, p, _ in candidates])
        prompt = "你是韩国电商分类专家。根据商品标签（优先）和标题，只选择最精确匹配的\n一个品类路径原文输出，不要解释。\n\n商品标签：" + tag + "\n商品标题：" + title + "\n\n候选品类：\n" + sample + "\n\n只输出最佳匹配的品类路径原文。"
        try:
            matched = deepseek_chat(prompt, max_tokens=200, temp=0.3)
        except:
            matched = candidates[0][1]
        for _, p, c in candidates:
            if p == matched.strip():
                best = c
                break
        else:
            best = candidates[0][2]

    matched_path = scored[0][1]
    for _, p, c in scored:
        if c == best:
            matched_path = p
            break
    return matched_path, best.get("esm_code", ""), str(best.get("auction", "")), str(best.get("gmarket", ""))
def phase1_price(prod, all_products, cfg):
    """Calculate selling price from median of same ParentSKU."""
    psku = prod.parent_sku
    prices = []
    for p in all_products:
        if p.parent_sku == psku:
            for _, _, pr in p.color_sizes:
                try:
                    prices.append(float(pr))
                except ValueError:
                    pass
    if not prices:
        return prod.price or "0"

    prices.sort()
    median = prices[len(prices) // 2]

    coeffs = cfg.get("price_coefficients",
                      [[0, 10000, 1.8], [10000, 20000, 1.8], [20000, 30000, 1.5],
                       [30000, 50000, 1.3], [50000, 999999999, 1.2]])
    multiplier = cfg.get("global_multiplier", 1.0)

    coefficient = 1.8
    for lo, hi, co in coeffs:
        if lo <= median < hi:
            coefficient = co
            break

    final = median * coefficient * multiplier
    final = round(final / 10) * 10
    return str(int(final))


def phase1_w_type(prod):
    n_colors = len(prod.colors)
    n_sizes = len(prod.sizes)
    if n_colors <= 1 and n_sizes <= 1:
        return "미사용"
    elif n_colors > 1 and n_sizes <= 1:
        return "단독형"
    elif n_colors <= 1 and n_sizes > 1:
        return "단독형"
    else:
        return "2개조합형"


def phase1_x_attr(prod, w):
    n_colors = len(prod.colors)
    n_sizes = len(prod.sizes)
    if w == "미사용":
        return "색상"
    elif w == "단독형":
        return "색상" if n_colors > 1 else "사이즈"
    else:
        return "색상,사이즈"


def phase1_y_list(prod, w, x):
    from config_manager import load_config
    qty = load_config().get("default_quantity", 50)
    size_noise = ["차트", "사이즈", "표를", "cm", "inch", "길이", "사이즈표", "상세"]
    lines = []
    for color, size, price in prod.color_sizes:
        color_kr = color  # GUI handler provides translate_fn
        # Filter noise from size
        size_clean = size
        for noise in size_noise:
            if noise.lower() in str(size).lower():
                size_clean = ""
                break

        if x == "색상,사이즈":
            lines.append(f"{color_kr},{size_clean},정상,노출,{qty},{qty}")
        elif x == "색상":
            lines.append(f"{color_kr},정상,노출,{qty},{qty}")
        elif x == "사이즈":
            lines.append(f"{size_clean},정상,노출,{qty},{qty}")
        else:
            lines.append(f"정상,노출,{qty},{qty}")
    return "\n".join(lines)


# ---- Phase 2: Concurrent per-batch ----

def _gen_main_image(prod, prompt, storage):
    """Generate main image using configured image API. Thread-safe.

    Retries 2 times on failure. Returns None if all attempts fail.
    Does NOT fall back to original image.
    """
    refs = [prod.main_img] + prod.extra_imgs[:3]  # max 4 total
    refs = list(dict.fromkeys(refs))  # dedup keep order
    last_err = None
    for attempt in range(3):  # initial + 2 retries
        try:
            img_bytes = generate_image(prompt, refs)
            url = storage.upload(img_bytes, f"main_{prod.parent_sku}.jpg")
            return url
        except Exception as e:
            last_err = e
            if attempt < 2:
                prod.logs.append(f"{time.strftime('%H:%M:%S')} 生图失败(重试{attempt+1}): {e}")
                time.sleep(3)
    prod.logs.append(f"{time.strftime('%H:%M:%S')} 生图失败(已重试2次): {last_err}")
    return None


def _gen_detail_html(prod, all_products, storage):
    """Generate AB column detail HTML. Thread-safe."""
    try:
        import io
        from PIL import Image
        # Collect images from all same-ParentSKU products
        img_urls = [prod.main_img] + prod.extra_imgs
        for p2 in all_products:
            if p2.parent_sku == prod.parent_sku:
                for u in p2.extra_imgs:
                    if u not in img_urls:
                        img_urls.append(u)
        # Dedup by stem, limit 20
        seen = set()
        deduped = []
        for u in img_urls:
            stem = _url_stem(u)
            if stem not in seen and len(deduped) < 20:
                seen.add(stem)
                deduped.append(u)

        html_parts = []
        for u in deduped:
            try:
                img_data = download_image(u)
                img = Image.open(io.BytesIO(img_data))
                w, h = img.size
                new_h = int(h * 800 / w) if w else 600
                img = img.resize((800, new_h), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                cloud_url = storage.upload(buf.getvalue(), f"detail_{int(time.time())}.jpg")
                html_parts.append(f'<P><img src="{cloud_url}" width="800"></P>')
            except Exception:
                html_parts.append(f'<P><img src="{u}" width="800"></P>')
        return "\n".join(html_parts)
    except Exception as e:
        prod.logs.append(f"{time.strftime('%H:%M:%S')} 详情图失败: {e}")
        return ""


def _collect_variant_imgs(prod):
    """Collect variant images for AA column."""
    main_stem = _url_stem(prod.main_img)
    variant_urls = []
    for u in prod.variant_imgs:
        if _url_stem(u) != main_stem:
            variant_urls.append(u)
    # If no variant images and single-color, add up to 2 extra images
    if not variant_urls and len(prod.colors) <= 1:
        for u in prod.extra_imgs[:2]:
            if _url_stem(u) != main_stem and u not in variant_urls:
                variant_urls.append(u)
    return ",".join(variant_urls)


# ---- Main Pipeline ----

def _get_category_zh(kr_path):
    """Translate a Korean category path to Chinese, with JSON cache."""
    if not kr_path:
        return ""
    cache = load_category_zh()
    if kr_path in cache:
        return cache[kr_path]
    # Translate
    try:
        prompt = f"""将以下韩文电商类目路径翻译为中文。只输出中文翻译，不要解释。
韩文：{kr_path}
中文："""
        zh = deepseek_chat(prompt, max_tokens=100, temp=0.3)
        zh = zh.strip()
        cache[kr_path] = zh
        save_category_zh(cache)
        return zh
    except Exception:
        return kr_path  # fall back to Korean


class ProcessingPipeline:
    """Orchestrates the full processing pipeline."""

    def __init__(self, source_path, template_path, output_path, prompt_key, mode_auto,
                 progress_callback=None, stat_callback=None, error_callback=None):
        self.source_path = source_path
        self.template_path = template_path
        self.output_path = output_path
        self.prompt_key = prompt_key
        self.mode_auto = mode_auto  # True = full auto, False = error confirm
        self.progress_cb = progress_callback or (lambda n, t, d: None)
        self.stat_cb = stat_callback or (lambda ds, hm, up: None)
        self.error_cb = error_callback or (lambda msg: True)  # returns True to continue
        self._stopped = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self.errors = []

    def stop(self):
        self._stopped = True
        self._pause_event.set()

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def run(self, products):
        """Run the full pipeline on a list of Products."""
        cfg = load_config()
        prompts = load_prompts()
        categories = load_categories(
            os.path.join(SKILL_DIR, "uploader", "카테고리목록(类别代码K列).xls"))
        banned_path = os.path.join(SKILL_DIR, "uploader", "违禁词gmk.txt")
        banned_words = load_banned_words(banned_path) if os.path.exists(banned_path) else []
        storage = create_storage_provider(cfg)
        prompt_text = prompts.get(self.prompt_key, prompts.get("generic", ""))
        batch_size = cfg.get("batch_size", 10)
        total = len(products)

        # Init output workbook
        twb, tws, fixed = init_output_workbook(self.template_path, self.output_path, cfg)
        done_skus = detect_completed(self.output_path)
        # Mark already-done products
        for prod in products:
            if prod.parent_sku in done_skus:
                prod.status = ProductStatus.DONE

        pending = [p for p in products if p.status != ProductStatus.DONE]
        start_row = len(done_skus)  # continue from last written row

        # Process in batches
        ds_count = hm_count = up_count = 0
        for batch_start in range(0, len(pending), batch_size):
            self._pause_event.wait()
            if self._stopped:
                break

            batch_end = min(batch_start + batch_size, len(pending))
            batch = pending[batch_start:batch_end]
            batch_idx = [products.index(p) for p in batch]

            # Phase 1: Serial
            for prod in batch:
                self._pause_event.wait()
                if self._stopped:
                    break
                real_idx = products.index(prod)
                try:
                    prod.status = ProductStatus.PHASE1_TITLE
                    self.progress_cb(real_idx + 1, total, f"#{real_idx + 1} 标题生成中...")
                    prod.ai_title = phase1_title(prod, banned_words, prompts)
                    ds_count += 1
                    prod.logs.append(f"{time.strftime('%H:%M:%S')} 标题生成完成")
                    self.progress_cb(real_idx + 1, total, f"#{real_idx + 1} 标题完成")

                    prod.status = ProductStatus.PHASE1_CATEGORY
                    self.progress_cb(real_idx + 1, total, f"#{real_idx + 1} 类目匹配中...")
                    if not prod.tag:
                        try:
                            keywords = haomingai_identify(prod.main_img)
                            prod.tag = deepseek_chat(
                                f"将以下品类关键词翻译为韩文关键词: {keywords}",
                                max_tokens=100, temp=0.3)
                            ds_count += 1
                            hm_count += 1
                        except:
                            pass
                    cat_path, k, l, m = phase1_category(prod, categories, self.prompt_key)
                    ds_count += 1
                    prod.result["K"] = k       # ESM code (number) — what Gmarket expects
                    prod.result["L"] = l       # auction code
                    prod.result["M"] = m       # Gmarket code
                    prod.result["K_path"] = cat_path  # Korean path for display
                    # Translate Korean category path to Chinese (cached)
                    cat_zh = _get_category_zh(cat_path)
                    prod.result["K_zh"] = cat_zh
                    prod.logs.append(f"{time.strftime('%H:%M:%S')} 类目匹配: {cat_path} ({cat_zh})")
                    self.progress_cb(real_idx + 1, total, f"#{real_idx + 1} 类目完成")

                    prod.status = ProductStatus.PHASE1_PRICE
                    price = phase1_price(prod, products, cfg)
                    prod.result["O"] = price
                    prod.result["P"] = price
                    prod.logs.append(f"{time.strftime('%H:%M:%S')} 价格计算: {price}")
                    self.progress_cb(real_idx + 1, total, f"#{real_idx + 1} Phase1完成")

                    prod.status = ProductStatus.PHASE1_DONE
                    prod.result["W"] = phase1_w_type(prod)
                    prod.result["X"] = phase1_x_attr(prod, prod.result["W"])
                    prod.result["Y"] = phase1_y_list(prod, prod.result["W"], prod.result["X"])
                except Exception as e:
                    prod.status = ProductStatus.FAILED
                    prod.logs.append(f"{time.strftime('%H:%M:%S')} Phase1 失败: {e}")
                    self.errors.append((products.index(prod), str(e)))

            # Phase 2: Concurrent (thread pool)
            if self._stopped:
                break

            z_results = {}
            ab_results = {}
            aa_results = {}

            def worker(prod):
                idx = products.index(prod)
                prod.status = ProductStatus.PHASE2_MAIN_IMG
                self.progress_cb(idx + 1, total, f"#{idx + 1} 生图中...")
                main_url = _gen_main_image(prod, prompt_text, storage)
                if main_url is None:
                    prod.status = ProductStatus.FAILED
                    z_results[idx] = ""
                    ab_results[idx] = ""
                    aa_results[idx] = ""
                    return 0, 0
                z_results[idx] = main_url

                prod.status = ProductStatus.PHASE2_DETAIL
                self.progress_cb(idx + 1, total, f"#{idx + 1} 详情图制作中...")
                ab_results[idx] = _gen_detail_html(prod, products, storage)
                detail_count = ab_results[idx].count("<P>") if ab_results[idx] else 1

                prod.status = ProductStatus.PHASE2_VARIANT
                aa_results[idx] = _collect_variant_imgs(prod)
                self.progress_cb(idx + 1, total, f"#{idx + 1} 生成完成")

                return 1, max(detail_count, 1)  # (hm_count, up_count)

            threads = []
            thread_results = []  # collect (hm_count, up_count) from each worker
            for prod in batch:
                def _runner(p=prod):
                    r = worker(p)
                    thread_results.append(r)
                t = threading.Thread(target=_runner)
                threads.append(t)
                t.start()

            for t in threads:
                t.join()
            for r in thread_results:
                hm_count += r[0]
                up_count += r[1]

            # Write batch
            for prod in batch:
                idx = products.index(prod)
                if prod.status != ProductStatus.FAILED:
                    prod.result["Z"] = z_results.get(idx, "")
                    prod.result["AA"] = aa_results.get(idx, "")
                    prod.result["AB"] = ab_results.get(idx, "")
                    prod.status = ProductStatus.DONE
                else:
                    prod.result["Z"] = "生成失败"
                write_product_row(tws, start_row, prod, fixed, 8)
                start_row += 1
                self.progress_cb(idx + 1, total, f"#{idx + 1} 已完成")

            twb.save(self.output_path)
            self.progress_cb(batch_end, total, f"批次 {batch_start//batch_size + 1} 保存完成")
            self.stat_cb(ds_count, hm_count, up_count)

            # Error confirm mode
            batch_errors = [e for e in self.errors if batch_start <= e[0] < batch_end]
            if batch_errors and not self.mode_auto:
                msg = f"本批 {len(batch_errors)} 个产品失败:\n"
                msg += "\n".join([f"  #{e[0] + 1}: {e[1][:60]}" for e in batch_errors[:5]])
                self._pause_event.clear()  # pause for user decision
                continue_ok = self.error_cb(msg)
                self._pause_event.set()
                if not continue_ok:
                    self._stopped = True
                    break

        twb.save(self.output_path)
        return ds_count, hm_count, up_count
