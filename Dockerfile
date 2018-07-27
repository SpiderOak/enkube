# Base
FROM python:3.6-alpine AS base

# Install build tools to be able to install jsonnet
RUN apk update && apk add --virtual build-dependencies \
    build-base

WORKDIR /app
COPY . .

# Install our packages
RUN pip install -r requirements.txt

RUN apk del build-dependencies \
    && rm -rf /var/cache/apk/*

# Enkube
FROM python:3.6-alpine

# Needed for CPython
RUN apk update && apk add \
    libstdc++ && \
    rm -rf /var/cache/apk/*

WORKDIR /app

COPY --from=base /app /app
COPY --from=base /root/.cache /root/.cache

# Install packages from cache
RUN pip install -r requirements.txt
# Install Enkube
RUN pip install -e .

# Remove extra stuff
RUN rm requirements.txt && rm -rf /root/.cache

WORKDIR /enkube

ENTRYPOINT ["enkube"]
