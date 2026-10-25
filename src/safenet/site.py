"""Один локальный сервер для сайта, API и изолированных демонстрационных данных."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from safenet.api import create_app
from safenet.http_contracts import UploadRecord, fingerprint
from safenet.storage import connect_database

ROOT = Path(__file__).resolve().parents[2]


def seed_demo(service):
    with connect_database(service.database) as connection:
        if connection.execute("SELECT 1 FROM cases LIMIT 1").fetchone():
            return
    texts = (
        "Синтетический пример. Взнос за участие 5000 тг. Рандомайзер выберет победителя. "
        "Комиссия организатора. https://demo-lottery.test",
        "Синтетикалық мысал. Қатысу тегін. Жеңімпаз кездейсоқ таңдалады.",
        "Синтетический пример. Казино. Переходи по ссылке, пополни депозит. Промокод DEMO42. https://demo-game.test",
        "Синтетикалық мысал. Кепілді табыс. Досыңды шақыр. Тек 3 орын қалды.",
        "Синтетический пример. Новости: казино рекламирует промокод. Памятка о рисках.",
        "Синтетический пример. Казино. Мой выигрыш. https://demo-game.test",
    )
    records = [UploadRecord(provenance="synthetic", synthetic=True, text=text) for text in texts]
    service.ingest(
        uuid5(NAMESPACE_URL, "safenet-local-demo-v1"),
        fingerprint(texts),
        [r.work() for r in records],
    )


def create_site(config_path=None, database_path=None, tessdata_path=None, *, seed=True):
    calibrated = ROOT / "config/calibrated.toml"
    backend = create_app(
        config_path or Path(os.environ.get("SAFENET_CONFIG", str(calibrated))),
        database_path,
        tessdata_path,
    )

    @asynccontextmanager
    async def lifespan(app):
        async with backend.router.lifespan_context(backend):
            if seed:
                await asyncio.to_thread(seed_demo, backend.state.service)
            yield

    site = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    site.mount("/backend", backend)
    site.mount("/", StaticFiles(directory=ROOT / "cloud/assets", html=True), name="site")
    return site


app = create_site()
