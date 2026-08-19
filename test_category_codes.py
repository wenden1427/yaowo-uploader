import unittest

import processor
from models import Product
from processor import ensure_upload_category_codes, phase1_category


class CategoryCodeTests(unittest.TestCase):
    def test_single_character_term_requires_an_exact_match(self):
        self.assertEqual(processor._semantic_similarity("다용도 접이식 테이블", "무"), 0.0)
        self.assertEqual(processor._semantic_similarity("무", "무"), 1.0)

    def test_ignores_esm_only_category_and_uses_uploadable_candidate(self):
        prod = Product(title="Cat House", tag="Cat House")
        categories = {
            "Pets>Cat House": {
                "esm_code": "esm-only",
                "auction": "",
                "gmarket": "",
            },
            "Pets>Cat": {
                "esm_code": "uploadable",
                "auction": "auction-code",
                "gmarket": "gmarket-code",
            },
        }

        path, esm_code, auction_code, gmarket_code = phase1_category(prod, categories)

        self.assertEqual(path, "Pets>Cat")
        self.assertEqual(esm_code, "uploadable")
        self.assertEqual(auction_code, "auction-code")
        self.assertEqual(gmarket_code, "gmarket-code")

    def test_returns_no_match_when_every_candidate_is_esm_only(self):
        prod = Product(title="Cat House", tag="Cat House")
        categories = {
            "Pets>Cat House": {
                "esm_code": "esm-only",
                "auction": "",
                "gmarket": "",
            },
        }

        self.assertEqual(phase1_category(prod, categories), ("", "", "", ""))

    def test_incomplete_platform_codes_are_blocked_before_export(self):
        with self.assertRaisesRegex(ValueError, "A/G"):
            ensure_upload_category_codes("esm", "", "")

    def test_sold_object_beats_a_referenced_appliance(self):
        prod = Product(
            title="Microwave oven wall shelf",
            tag="steel, kitchen",
            subject_profile={
                "sold_object": "wall-mounted kitchen storage shelf",
                "sold_object_ko": "주방 선반",
                "sold_object_zh": "厨房置物架",
                "buyer_receives": "a metal storage shelf",
                "primary_function": "kitchen storage and support",
                "referenced_objects": ["microwave", "oven"],
                "confidence": 0.98,
            },
        )
        categories = {
            "Appliances>Kitchen Appliances>Microwave": self._codes("microwave"),
            "Home>Kitchen>Storage>Kitchen Shelf": self._codes("shelf"),
        }

        result = phase1_category(prod, categories)

        self.assertEqual(result[0], "Home>Kitchen>Storage>Kitchen Shelf")
        self.assertEqual(result[1:], ("shelf", "shelf-a", "shelf-g"))
        self.assertEqual(prod.result["_category_deepseek_calls"], 0)

    def test_sold_object_beats_its_contents_across_product_types(self):
        cases = [
            (
                {
                    "sold_object": "portable spice grinder",
                    "buyer_receives": "a handheld grinder",
                    "primary_function": "grinding seasonings",
                    "category_terms_ko": ["절구", "맷돌"],
                    "category_terms_zh": ["捣臼", "石磨"],
                    "referenced_objects": ["pepper", "salt"],
                    "confidence": 0.96,
                },
                {
                    "Food>Seasonings>Pepper": self._codes("pepper"),
                    "Home>Kitchen Tools>Mortar and Mill": self._codes("grinder"),
                },
                "Home>Kitchen Tools>Mortar and Mill",
            ),
            (
                {
                    "sold_object": "hanging storage basket",
                    "buyer_receives": "an under-shelf basket",
                    "primary_function": "organizing stored items",
                    "referenced_objects": ["underwear", "wardrobe", "shelf"],
                    "confidence": 0.97,
                },
                {
                    "Books>Art>Design": self._codes("design"),
                    "Home>Storage>Basket": self._codes("basket"),
                },
                "Home>Storage>Basket",
            ),
        ]

        for profile, categories, expected in cases:
            with self.subTest(expected=expected):
                prod = Product(title="noisy source title", tag="origin material", subject_profile=profile)
                self.assertEqual(phase1_category(prod, categories)[0], expected)

    def test_referenced_object_is_allowed_inside_a_compound_sold_item_category(self):
        cases = [
            (
                {
                    "sold_object_ko": "고양이 자동 레이저 장난감",
                    "category_terms_ko": ["고양이 장난감", "레이저 장난감"],
                    "referenced_objects": ["고양이"],
                    "confidence": 0.98,
                },
                {
                    "반려동물용품>장난감/훈련용품>레이저": self._codes("generic"),
                    "반려동물용품>고양이용품>고양이장난감>고양이레이저": self._codes("cat"),
                },
                "반려동물용품>고양이용품>고양이장난감>고양이레이저",
            ),
            (
                {
                    "sold_object_ko": "슬라이딩 트레이 선반",
                    "buyer_receives": "냉장고 내부 공간 확장 슬라이딩 트레이 선반",
                    "primary_function": "냉장고 내부 수납 공간 최적화 및 정리",
                    "category_terms_ko": [
                        "냉장고 수납용품", "식료품 정리함", "슬라이드 트레이", "선반 액세서리"
                    ],
                    "referenced_objects": ["냉장고"],
                    "confidence": 0.98,
                },
                {
                    "수집/종교용품>수집용품>모형/프라모델>보관용품": self._codes("storage"),
                    "리빙>주방용품>주방정리용품>주방선반": self._codes("shelf"),
                    "리빙>주방용품>주방정리용품>냉장고선반/트레이": self._codes("fridge"),
                },
                "리빙>주방용품>주방정리용품>냉장고선반/트레이",
            ),
        ]

        for profile, categories, expected in cases:
            with self.subTest(expected=expected):
                scored = processor._score_categories_from_subject(profile, categories)
                self.assertEqual(scored[0][1], expected)

    def test_generic_model_choice_is_refined_to_supported_deeper_leaf(self):
        generic_path = "반려동물용품>장난감/훈련용품>레이저"
        specific_path = "반려동물용품>고양이용품>고양이장난감>고양이레이저"
        generic_codes = self._codes("generic")
        specific_codes = self._codes("cat")
        prod = Product(title="고양이 자동 회전 레이저 장난감")
        profile = {
            "sold_object_ko": "고양이 레이저 장난감",
            "category_terms_ko": ["레이저 장난감", "고양이 장난감"],
            "evidence": ["고양이", "레이저 장난감"],
        }

        result = processor._refine_to_supported_specific_category(
            prod,
            profile,
            (generic_path, generic_codes),
            [
                (0.9, generic_path, generic_codes),
                (0.8, specific_path, specific_codes),
            ],
        )

        self.assertEqual(result[0], specific_path)

    def test_ambiguous_subject_reuses_at_most_one_category_call(self):
        original = processor.deepseek_chat
        calls = []
        try:
            processor.deepseek_chat = lambda *args, **kwargs: calls.append((args, kwargs)) or '''{
              "candidate_id": "C01",
              "same_sold_object": true,
              "confidence": 0.9,
              "evidence": "same physical item"
            }'''
            prod = Product(
                title="holder",
                subject_profile={
                    "sold_object": "multipurpose holder",
                    "buyer_receives": "a holder",
                    "primary_function": "holding objects",
                    "referenced_objects": [],
                    "confidence": 0.8,
                },
            )
            categories = {
                "Home>Holder": self._codes("holder-1"),
                "Office>Holder": self._codes("holder-2"),
            }

            result = phase1_category(prod, categories)

            self.assertIn(result[0], categories)
            self.assertLessEqual(len(calls), 1)
            self.assertEqual(prod.result["_category_deepseek_calls"], len(calls))
            if calls:
                prompt = calls[0][0][0]
                self.assertIn("multipurpose holder", prompt)
                self.assertNotIn("origin material", prompt)
        finally:
            processor.deepseek_chat = original

    def test_adult_womens_shoe_cannot_enter_children_shoe_category(self):
        prod = Product(
            title="2025 봄 가을 여성 플랫폼 워킹 슈즈",
            tag="여성, 성인, 레이스업, 메쉬",
            subject_profile={
                "sold_object": "women's platform walking shoes",
                "sold_object_ko": "여성 운동화",
                "category_terms_ko": ["운동화", "스니커즈"],
                "audience_age": "adult",
                "audience_gender": "female",
                "confidence": 0.99,
            },
        )
        categories = {
            "유아동>유아동신발>운동화": self._codes("children"),
            "신발>남녀캐주얼화>스니커즈": self._codes("adult"),
        }

        result = phase1_category(prod, categories)

        self.assertEqual(result[0], "신발>남녀캐주얼화>스니커즈")
        self.assertEqual(prod.result["_category_deepseek_calls"], 0)
        self.assertEqual(prod.result["_category_audience"]["age"], "adult")
        self.assertEqual(prod.result["_category_audience"]["gender"], "female")
        self.assertEqual(prod.result["_category_audience_filtered"], 1)

    def test_matching_child_audience_promotes_the_correct_leaf_into_shortlist(self):
        prod = Product(
            title="儿童迷你单肩包 女童可爱斜挎包",
            tag="유아동 여아 숄더백",
            subject_profile={
                "sold_object_ko": "미니 숄더백",
                "category_terms_ko": ["소품", "가방", "여아용품", "패션액세서리"],
                "audience_age": "child",
                "audience_gender": "female",
                "confidence": 0.95,
            },
        )
        expected = "유아동>유아동패션잡화>유아동가방>유아동숄더백"
        categories = {
            "패션잡화>가방>여성가방>숄더백": self._codes("adult"),
            "유아동>유아동패션잡화>유아동가방>미아방지가방": self._codes("safety"),
            expected: self._codes("shoulder"),
        }

        scored = processor._score_categories_from_subject(prod.subject_profile, categories)
        filtered, constraints, _ = processor._filter_categories_by_audience(
            prod, prod.subject_profile, scored
        )

        self.assertEqual(constraints, {"age": "child", "gender": "female"})
        self.assertIn(expected, [path for _, path, _ in filtered])
        self.assertLessEqual(
            [path for _, path, _ in filtered].index(expected),
            1,
        )
        self.assertNotIn(
            "패션잡화>가방>여성가방>숄더백",
            [path for _, path, _ in filtered],
        )

    def test_category_ai_failure_falls_back_without_blank_codes(self):
        original = processor.deepseek_chat
        try:
            processor.deepseek_chat = lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("temporary network error")
            )
            prod = Product(
                title="foldable phone tripod",
                subject_profile={
                    "sold_object": "foldable phone tripod",
                    "category_terms_ko": ["휴대폰삼각대"],
                    "confidence": 0.95,
                },
            )
            categories = {
                "디지털>휴대폰용품>휴대폰삼각대": self._codes("tripod"),
                "디지털>카메라용품>삼각대": self._codes("camera"),
            }

            result = phase1_category(prod, categories)

            self.assertIn(result[0], categories)
            self.assertTrue(all(result[1:]))
            self.assertTrue(any("使用本地最高分" in log for log in prod.logs))
        finally:
            processor.deepseek_chat = original

    def test_explicit_title_outweighs_conflicting_noisy_tag_audience(self):
        prod = Product(
            title="남성용 아웃도어 하이킹 신발",
            tag="남자, 아기, 하이킹 신발",
            subject_profile={
                "sold_object": "하이킹 신발",
                "audience_age": "unknown",
                "audience_gender": "unknown",
                "confidence": 0.95,
            },
        )

        constraints = processor._source_audience_constraints(
            prod, prod.subject_profile
        )

        self.assertEqual(constraints, {"age": "adult", "gender": "male"})

    def test_specific_maternity_leaf_overrides_children_root_folder(self):
        original = processor._CATEGORY_ZH_MAP
        try:
            path = "유아동>출산/임산부용품>임부복>임산부원피스"
            processor._CATEGORY_ZH_MAP = {
                path: "婴幼儿童>孕产妇用品>孕妇装>孕妇连衣裙"
            }

            signals = processor._candidate_audience_signals(path)

            self.assertEqual(signals["age"], {"adult"})
            self.assertEqual(signals["gender"], {"female"})
        finally:
            processor._CATEGORY_ZH_MAP = original

    def test_subject_confidence_does_not_bypass_ambiguous_category_choice(self):
        original = processor.deepseek_chat
        calls = []
        try:
            processor.deepseek_chat = lambda *args, **kwargs: (
                calls.append((args, kwargs))
                or '{"candidate_id":"C02","same_sold_object":true}'
            )
            prod = Product(
                title="multipurpose holder",
                subject_profile={
                    "sold_object": "multipurpose holder",
                    "category_terms_ko": ["holder"],
                    "audience_age": "unknown",
                    "audience_gender": "unknown",
                    "confidence": 1.0,
                },
            )
            categories = {
                "Home>Holder": self._codes("home"),
                "Office>Holder": self._codes("office"),
            }

            result = phase1_category(prod, categories)

            self.assertEqual(result[0], "Office>Holder")
            self.assertEqual(len(calls), 1)
            self.assertIn("Source title: multipurpose holder", calls[0][0][0])
            self.assertIn("Audience constraints", calls[0][0][0])
        finally:
            processor.deepseek_chat = original

    def test_candidate_pool_adds_uploadable_ancestors_of_recalled_leaves(self):
        parent = "반려동물용품>강아지용품>강아지하우스/울타리"
        leaf = parent + ">강아지집"
        scored = [
            (1.0, leaf, self._codes("leaf")),
            (0.9, "반려동물용품>기타", self._codes("other")),
            (0.2, parent, self._codes("parent")),
        ]

        pool = processor._category_candidate_pool(
            scored, base_limit=2, ancestor_limit=3
        )

        self.assertEqual([item[1] for item in pool[:2]], [leaf, "반려동물용품>기타"])
        self.assertIn(parent, [item[1] for item in pool])

    def test_head_noun_overrides_a_modifier_only_category(self):
        flat = "신발>여성화>플랫슈즈"
        sneaker = "신발>남녀캐주얼화>스니커즈"
        profile = {
            "head_noun_ko": "스니커즈",
            "head_noun_zh": "运动鞋",
            "sold_object_ko": "여성용 스니커즈",
            "category_terms_zh": ["运动鞋"],
        }

        result = processor._refine_to_head_noun_category(
            profile,
            (flat, self._codes("flat")),
            [
                (1.2, flat, self._codes("flat")),
                (0.8, sneaker, self._codes("sneaker")),
            ],
        )

        self.assertEqual(result[0], sneaker)

    def test_head_noun_tie_prefers_matching_child_audience(self):
        child = "유아동>유아동패션잡화>유아동가방>유아동숄더백"
        adult = "패션잡화>가방>남여공용가방>숄더백"
        profile = {
            "head_noun_ko": "숄더백",
            "sold_object_ko": "유아용 미니 숄더백",
        }

        result = processor._refine_to_head_noun_category(
            profile,
            (adult, self._codes("adult")),
            [
                (1.0, adult, self._codes("adult")),
                (0.9, child, self._codes("child")),
            ],
            audience_constraints={"age": "child", "gender": "female"},
        )

        self.assertEqual(result[0], child)

    def test_short_head_noun_does_not_match_an_unrelated_compound(self):
        profile = {
            "head_noun_ko": "침대",
            "sold_object_ko": "반려동물 침대",
        }

        support = processor._head_noun_leaf_support(
            profile, "스포츠/레저용품>낚시용품>받침대"
        )

        self.assertLess(support, 0.82)

    def test_head_noun_topic_prefix_does_not_validate_a_book_category(self):
        profile = {
            "head_noun_ko": "반려견 침대",
            "head_noun_zh": "宠物狗床",
            "sold_object_ko": "반려견 침대",
            "sold_object_zh": "宠物狗床",
        }

        support = processor._head_noun_leaf_support(
            profile, "도서>여행/취미>반려동물"
        )

        self.assertEqual(support, 0.0)

    def test_reference_only_appliance_is_replaced_by_sold_rack_candidate(self):
        profile = {
            "head_noun_ko": "선반",
            "sold_object_ko": "전자레인지 벽걸이 선반",
            "category_terms_ko": ["벽걸이선반", "전자레인지받침대"],
            "referenced_objects": ["전자레인지 (Microwave)"],
        }
        appliance = ("가전>주방가전>전자레인지", self._codes("microwave"))
        rack = ("리빙>주방용품>주방정리용품>주방선반", self._codes("rack"))
        candidates = [
            (1.0, appliance[0], appliance[1]),
            (0.8, rack[0], rack[1]),
        ]

        result = processor._refine_away_from_referenced_object(
            profile, appliance, candidates
        )

        self.assertEqual(result[0], rack[0])

    def test_unsupported_conflicting_head_noun_cannot_override_subject(self):
        profile = {
            "head_noun_ko": "화장실",
            "sold_object_ko": "애완동물 침대",
            "category_terms_ko": ["고양이 침대", "펫 침대"],
        }

        support = processor._head_noun_leaf_support(
            profile, "반려동물용품>고양이용품>고양이화장실"
        )

        self.assertEqual(support, 0.0)

    def test_unrelated_cross_root_choice_falls_back_to_dominant_product_root(self):
        profile = {
            "head_noun_ko": "반려견 침대",
            "sold_object_ko": "반려견 침대",
        }
        book = ("도서>여행/취미>반려동물", self._codes("book"))
        scored = [
            (0.9, "반려동물용품>기타반려동물용품", self._codes("pet-1")),
            (0.8, "반려동물용품>하우스/안전용품", self._codes("pet-2")),
            (0.7, "반려동물용품>하우스/안전용품>매트", self._codes("pet-3")),
            (0.6, book[0], book[1]),
        ]

        result = processor._refine_to_dominant_root(profile, book, scored)

        self.assertTrue(result[0].startswith("반려동물용품>"))

    def test_unrelated_choice_falls_back_when_top_two_agree_on_root(self):
        profile = {
            "head_noun_ko": "플랫폼 워킹 슈즈",
            "sold_object_ko": "플랫폼 워킹 슈즈",
            "category_terms_ko": ["여성 신발", "운동화"],
        }
        shirt = ("의류>여성의류>여성 티셔츠", self._codes("shirt"))
        scored = [
            (0.84, "신발>여성화>플랫슈즈", self._codes("shoe-1")),
            (0.80, "신발>스포츠화>기타스포츠화", self._codes("shoe-2")),
            (0.72, shirt[0], shirt[1]),
            (0.71, "의류>여성의류>여성 팬츠", self._codes("pants")),
        ]

        result = processor._refine_to_dominant_root(profile, shirt, scored)

        self.assertTrue(result[0].startswith("신발>"))

    def test_correct_model_root_is_not_overridden_by_many_generic_candidates(self):
        profile = {
            "head_noun_ko": "플랫폼 워킹 슈즈",
            "sold_object_ko": "플랫폼 워킹 슈즈",
            "category_terms_ko": ["여성 신발", "운동화"],
        }
        sneaker = ("신발>남녀캐주얼화>패션운동화", self._codes("sneaker"))
        scored = [
            (0.84, "신발>여성화>플랫슈즈", self._codes("shoe-1")),
            (0.80, "신발>스포츠화>기타스포츠화", self._codes("shoe-2")),
        ] + [
            (0.72 - index / 1000, f"의류>여성의류>여성 의류 {index}", self._codes(f"clothes-{index}"))
            for index in range(20)
        ]

        result = processor._refine_to_dominant_root(profile, sneaker, scored)

        self.assertEqual(result[0], sneaker[0])

    @staticmethod
    def _codes(value):
        return {
            "esm_code": value,
            "auction": value + "-a",
            "gmarket": value + "-g",
        }


if __name__ == "__main__":
    unittest.main()
