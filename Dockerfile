# We have to use this as a base because the jsonnet package reuquires 'make' to
# build, which blows up the image size.  Otherwise we could just use alpine
FROM python:3.6 AS base

WORKDIR /app
COPY . .

# Install our packages
RUN pip install -r requirements.txt

# Enkube
FROM python:3.6-slim

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
