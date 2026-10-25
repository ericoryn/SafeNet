import json
import os
from urllib.error import URLError
from urllib.request import urlopen

import streamlit as st
from pydantic import ValidationError

from safenet import DISCLAIMER
from safenet.contracts import HealthResponse

st.set_page_config(page_title="SafeNet KZ", page_icon="🛡️")
st.title("SafeNet KZ")
st.info(DISCLAIMER)
st.caption("Прототип для аналитика · API: этап 6")
st.warning(
    "API анализа и истории решений готов. Интерфейс очереди и карточек материалов "
    "будет добавлен на этапе 7."
)
st.button("Проверить подключение")

base_url = os.environ.get("SAFENET_API_URL", "http://127.0.0.1:8000").rstrip("/")
try:
    with urlopen(f"{base_url}/health", timeout=3) as response:
        health = HealthResponse.model_validate(json.load(response))
    if health.status == "ok":
        st.success("API и база данных доступны.")
        st.caption(
            f"Версия схемы: {health.schema_version} · Версия приложения: {health.code_version}"
        )
    else:
        st.error("API или база данных недоступны. Повторите проверку подключения.")
except (URLError, OSError, ValueError, ValidationError):
    st.error("API недоступен. Запустите сервер и повторите проверку подключения.")
