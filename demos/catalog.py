"""Explicit, import-safe scene catalog; URL values never become arbitrary imports."""

SCENES = {
    "home": "Demo gallery / デモ一覧",
    "customer": "Customer / 顧客",
    "branch": "Branch staff / 支店担当者",
    "aml": "AML analyst / AML担当者",
    "onboarding": "Account opening / 口座開設",
    "lending": "Loan officer / 融資担当者",
    "sales": "Sales manager / 営業管理",
    "opportunity": "Sales representative / 商談登録",
    "attendance": "Employee / 勤怠",
    "events": "Kafka / 振込イベント",
    "options": "QuantLib / オプション",
    "generator": "Data tools / テストデータ",
    "portfolio": "Portfolio / 健全性スコア",
    "credit": "Credit risk / 与信審査",
}

EXTRA_RENDERERS = {
    "home": ("demos.home", "render"),
    "onboarding": ("demos.onboarding", "render"),
    "lending": ("demos.lending", "render"),
    "sales": ("demos.crm", "render_manager"),
    "opportunity": ("demos.crm", "render_opportunity"),
    "attendance": ("demos.attendance", "render"),
    "events": ("demos.events", "render"),
    "options": ("demos.options", "render"),
    "generator": ("demos.generator", "render"),
    "portfolio": ("demos.portfolio", "render"),
    "credit": ("demos.credit", "render"),
}


def render_extra_scene(scene: str) -> bool:
    from importlib import import_module

    target = EXTRA_RENDERERS.get(scene)
    if target is None:
        return False
    module, function = target
    getattr(import_module(module), function)()
    return True
