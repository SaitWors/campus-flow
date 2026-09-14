# syntax=docker/dockerfile:1
FROM libretranslate/libretranslate:v1.9.6@sha256:1de2d7056bb8ad607a412f4563d9abe324ff632b43b5be9428bcc8e213aebb32
ENV LT_LOAD_ONLY=en,ru \
    LT_DISABLE_WEB_UI=true \
    LT_DISABLE_FILES_TRANSLATION=true \
    LT_THREADS=1 \
    LT_CHAR_LIMIT=120 \
    LT_BATCH_LIMIT=1 \
    ARGOS_CHUNK_TYPE=MINISBD \
    OMP_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY scripts/install_ru_en_model.py /tmp/install_ru_en_model.py
# Upstream assumes at least two directions and otherwise attempts a runtime
# download. One directed package is sufficient; keep its offline startup intact.
RUN ./venv/bin/python -c "from pathlib import Path; p=Path('libretranslate/init.py'); s=p.read_text(); assert 'len(package.get_installed_packages()) < 2' in s; p.write_text(s.replace('len(package.get_installed_packages()) < 2', 'len(package.get_installed_packages()) < 1'))"
RUN --mount=type=cache,id=campus-ru-en-v1,target=/tmp/model-cache,uid=1032,gid=1032 \
    ./venv/bin/python /tmp/install_ru_en_model.py \
    && ./venv/bin/python -c "from argostranslate.translate import translate; value=translate('Технологии баз данных','ru','en'); assert value and value != 'Технологии баз данных'"
