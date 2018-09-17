FROM python:3.7-alpine as reqs

WORKDIR /install
COPY requirements.txt /enkube/requirements.txt

RUN pip install -U pip \
&& apk update \
&& apk add --no-cache --virtual=build-dependencies \
    build-base \
    curl \
&& mkdir bin \
&& curl -sL https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl > bin/kubectl \
&& chmod 0755 bin/kubectl \
&& pip install --install-option="--prefix=/install" -r /enkube/requirements.txt

FROM python:3.7-alpine

RUN apk add --no-cache \
    libstdc++ \
&& pip install -U pip

COPY --from=reqs /install /usr/local
COPY . /enkube

RUN pip install -e /enkube

ENTRYPOINT ["enkube"]
