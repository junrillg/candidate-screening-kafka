FROM dpage/pgadmin4:9.18@sha256:c332c5f6dfba995d9ebc4af261d93506d6876085d712eaaa3defc8dd1a3f26de
USER 0
RUN apk add --no-cache sudo=1.9.17_p2-r2 \
    && printf '%s\n' 'urllib3==2.8.0 --hash=sha256:0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3' > /tmp/urllib3-requirements.txt \
    && /venv/bin/python -m pip install --no-cache-dir --no-deps --require-hashes -r /tmp/urllib3-requirements.txt \
    && /venv/bin/python -m pip check \
    && /usr/local/bin/python3 -m pip check \
    && rm /tmp/urllib3-requirements.txt \
    && /usr/local/bin/python3 -m pip uninstall --yes pip
# pip and its vendored packages are build tooling; pgAdmin's runtime does not import them.
USER 5050
