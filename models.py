# Author: Administrator
# Created: 2026-05-25
"""Data models for 耀我科技上传器 v2.0."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class ProductStatus(Enum):
    """Product processing lifecycle."""
    PENDING = "待处理"
    PHASE1_TITLE = "标题生成中"
    PHASE1_CATEGORY = "类目匹配中"
    PHASE1_PRICE = "价格计算中"
    PHASE1_DONE = "预处理完成"
    PHASE2_MAIN_IMG = "主图生成中"
    PHASE2_VARIANT = "变种图处理中"
    PHASE2_DETAIL = "详情图制作中"
    DONE = "已完成"
    FAILED = "失败"


@dataclass
class Product:
    """A single product row in the Gmarket upload form."""
    parent_sku: str = ""
    title: str = ""
    ai_title: str = ""
    tag: str = ""
    colors: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    color_sizes: list[tuple] = field(default_factory=list)
    main_img: str = ""
    extra_imgs: list[str] = field(default_factory=list)
    variant_imgs: list[str] = field(default_factory=list)
    desc_images: list[str] = field(default_factory=list)
    platform: str = "shein"
    price: str = ""
    url: str = ""
    status: ProductStatus = ProductStatus.PENDING
    result: dict = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    retry_count: dict = field(default_factory=dict)


@dataclass
class Batch:
    """A batch of products processed together."""
    products: list[Product] = field(default_factory=list)
    batch_num: int = 0
    phase1_done: bool = False
    phase2_done: bool = False
