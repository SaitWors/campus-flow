# Official release. Download and warm RU/EN models at build time.
FROM libretranslate/libretranslate:v1.9.6
ENV LT_LOAD_ONLY=en,ru \
    LT_DISABLE_WEB_UI=true \
    LT_DISABLE_FILES_TRANSLATION=true \
    LT_THREADS=1 \
    LT_CHAR_LIMIT=120 \
    LT_BATCH_LIMIT=1 \
    ARGOS_CHUNK_TYPE=MINISBD \
    OMP_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN ./venv/bin/python scripts/install_models.py --load_only_lang_codes en,ru \
    && ./venv/bin/python -c "from argostranslate.translate import translate; value=translate('Технологии баз данных','ru','en'); assert value and value != 'Технологии баз данных'"
