"""Category regressions: bounded Flash calls, semantic roles, and safe IDs."""
from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from unittest.mock import patch

import processor
from models import Product


def codes(label):
    return {"esm_code": label, "auction": label + "-a", "gmarket": label + "-g"}


def response_for(prompt, path, **extra):
    candidates = json.loads(prompt.split("Candidates: ", 1)[1])
    ident = next(ident for ident, value in candidates.items() if value[0] == path)
    return json.dumps({"candidate_id": ident, "same_sold_object": True, **extra})


class CategoryReviewTests(unittest.TestCase):
    def test_confirmed_accessory_keeps_its_target_with_one_flash_call(self):
        cases = [
            ("camera sling bag", "sling bag", "camera bag", "Electronics>Camera Bag", "Fashion>Sling Bag"),
            ("vacuum replacement filter", "filter", "vacuum filter", "Appliances>Vacuum Filter", "Bathroom>Water Filter"),
            ("laptop protective sleeve", "protective sleeve", "laptop sleeve", "Computers>Laptop Sleeve", "Baby>Protective Sleeve"),
            ("bicycle headlight", "headlight", "bicycle light", "Sports>Bicycle Light", "Automotive>Headlight"),
        ]
        for title, head, sold, good, wrong in cases:
            with self.subTest(title=title), patch.object(processor, "_CATEGORY_ZH_MAP", {}):
                prod = Product(title=title, subject_profile={
                    "head_noun_ko": head, "sold_object": sold,
                    "category_terms_ko": [head, sold], "confidence": 0.95,
                })
                taxonomy = {good: codes("good"), wrong: codes("wrong")}
                with patch.object(processor, "deepseek_chat", side_effect=lambda prompt, **kw: response_for(prompt, good)) as chat:
                    result = processor.phase1_category(prod, taxonomy)
                self.assertEqual(result, (good, "good", "good-a", "good-g"))
                self.assertEqual(prod.result["_category_match_status"], "ai_confirmed")
                self.assertEqual(chat.call_count, 1)
                self.assertEqual(chat.call_args.kwargs["model"], "qwen3.7-flash")

    def test_source_can_correct_a_wrong_subject_analysis(self):
        good, wrong = "Home>Side Table", "Home>Sofa"
        prod = Product(title="sofa side table metal snack table", subject_profile={
            "sold_object": "sofa", "category_terms_ko": ["sofa", "side table"], "confidence": 0.98,
        })
        with patch.object(processor, "deepseek_chat", side_effect=lambda prompt, **kw: response_for(prompt, good)):
            result = processor.phase1_category(prod, {good: codes("table"), wrong: codes("sofa")})
        self.assertEqual(result[0], good)

    def test_negative_or_malformed_compatibility_rejects_candidate(self):
        for value in (False, "false", "true", 1, None):
            with self.subTest(value=value):
                payload = json.dumps({"candidate_id": "C001", "same_sold_object": value})
                with patch.object(processor, "deepseek_chat", return_value=payload):
                    result = processor._select_category_with_deepseek(Product(), {}, [(1, "Home>Box", codes("box"))])
                self.assertIsNone(result)

    def test_candidate_padding_is_unambiguous_and_bounded(self):
        candidates = [(1.0, "Home>Storage", codes("storage"))]
        for ident in ("C1", "C01", "C001", "C000001"):
            with self.subTest(ident=ident):
                payload = json.dumps({"candidate_id": ident, "same_sold_object": True})
                with patch.object(processor, "deepseek_chat", return_value=payload):
                    result = processor._select_category_with_deepseek(Product(), {}, candidates)
                self.assertEqual(result[0], "Home>Storage")
        for ident in ("C0", "C002", "C-1", "C1 or C2", "C001-extra", "C1000000"):
            with self.subTest(ident=ident):
                payload = json.dumps({"candidate_id": ident, "same_sold_object": True})
                with patch.object(processor, "deepseek_chat", return_value=payload):
                    self.assertIsNone(processor._select_category_with_deepseek(Product(), {}, candidates))

    def test_neighbor_recall_preserves_synonyms_and_audience_exclusions(self):
        first, neutral = "신발>여성화>플랫슈즈", "신발>남녀캐주얼화>스니커즈"
        wrong, unrelated = "신발>남성화>샌들", "Books>Furniture>Design"
        taxonomy = {p: codes(str(i)) for i, p in enumerate((first, neutral, wrong, unrelated))}
        with patch.object(processor, "_CATEGORY_ZH_MAP", {}):
            result = processor._expand_category_candidates(
                [(1, first, taxonomy[first])], taxonomy, {"age": "adult", "gender": "female"})
        self.assertEqual([p for _, p, _ in result], [first, neutral])

    def test_neighbor_recall_is_bounded_and_deduplicated(self):
        taxonomy = {f"Root>Group{i}>Item{j}": codes(f"{i}-{j}") for i in range(5) for j in range(75)}
        seeds = [(1, p, c) for p, c in taxonomy.items() if p.endswith("Item0")]
        result = processor._expand_category_candidates(seeds, taxonomy, {}, limit=40)
        self.assertEqual(len(result), 40)
        self.assertEqual(len({p for _, p, _ in result}), 40)
        self.assertEqual(result[:len(seeds)], seeds)

    def test_rejection_never_adds_a_call_or_a_model(self):
        prod = Product(title="storage box", subject_profile={"sold_object": "box", "confidence": 0.8})
        taxonomy = {"Home>Box": codes("home"), "Office>Box": codes("office")}
        payload = json.dumps({"candidate_id": "", "same_sold_object": False, "recall_terms_ko": ["box"]})
        with patch.object(processor, "deepseek_chat", return_value=payload) as chat:
            result = processor.phase1_category(prod, taxonomy)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(chat.call_args.kwargs["model"], "qwen3.7-flash")
        self.assertTrue(all(result))
        self.assertEqual(prod.result["_category_match_status"], "local_fallback")
        self.assertTrue(any("请人工核对" in log for log in prod.logs))

    def test_previous_review_is_not_reused_after_network_failure(self):
        prod = Product(title="storage box", subject_profile={"sold_object": "box", "confidence": 0.8},
                       result={"_category_model_review": {"same_sold_object": True}})
        taxonomy = {"Home>Box": codes("home"), "Office>Box": codes("office")}
        with patch.object(processor, "deepseek_chat", side_effect=OSError("offline")) as chat:
            result = processor.phase1_category(prod, taxonomy)
        self.assertTrue(all(result))
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(prod.result["_category_match_status"], "local_fallback")
        self.assertNotIn("_category_model_review", prod.result)

    def test_noisy_retrieval_synonyms_are_not_repeated_to_the_selector(self):
        prod = Product(title="tissue box")
        profile = {"sold_object_zh": "纸巾盒", "category_terms_ko": ["WRONG_RETRIEVAL_SYNONYM"]}
        with patch.object(processor, "deepseek_chat", return_value='{"candidate_id":"C001","same_sold_object":true}') as chat:
            processor._select_category_with_deepseek(prod, profile, [(1, "Home>Tissue Box", codes("box"))])
        self.assertNotIn("WRONG_RETRIEVAL_SYNONYM", chat.call_args.args[0])
        self.assertIn("纸巾盒", chat.call_args.args[0])

    def test_parallel_products_keep_their_candidate_mapping(self):
        taxonomy = {"Home>Box": codes("home"), "Office>Box": codes("office")}

        def chat(prompt, **kwargs):
            target = "Office>Box" if "Source title: office box" in prompt else "Home>Box"
            return response_for(prompt, target, evidence=target)

        def run(index):
            target = "Office>Box" if index % 2 else "Home>Box"
            prod = Product(title="office box" if index % 2 else "home box", subject_profile={"sold_object": "box", "confidence": 0.8})
            result = processor.phase1_category(prod, taxonomy)
            return result[0], prod.result["_category_model_review"]["evidence"], target

        with patch.object(processor, "deepseek_chat", side_effect=chat), ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(32)))
        self.assertTrue(all(selected == evidence == target for selected, evidence, target in results))


if __name__ == "__main__":
    unittest.main()
