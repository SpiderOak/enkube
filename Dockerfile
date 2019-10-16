# Copyright 2018 SpiderOak, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM python:3.7-alpine as reqs

WORKDIR /install
COPY requirements.txt /enkube/requirements.txt

ENV RELEASE=v1.16.2
ENV HELM_RELEASE=v2.14.3

RUN pip install -U pip \
&& apk add --no-cache --virtual=build-dependencies \
    build-base \
    curl \
&& mkdir bin \
&& curl -sL https://storage.googleapis.com/kubernetes-release/release/${RELEASE}/bin/linux/amd64/kubectl > bin/kubectl \
&& chmod 0755 bin/kubectl \
&& curl -sL https://get.helm.sh/helm-${HELM_RELEASE}-linux-amd64.tar.gz | tar -C /tmp -xzv \
&& mv /tmp/linux-amd64/helm bin \
&& pip install --install-option="--prefix=/install" -r /enkube/requirements.txt

FROM python:3.7-alpine

RUN apk add --no-cache \
    libstdc++ \
&& pip install -U pip

COPY --from=reqs /install /usr/local
COPY . /enkube

RUN pip install -e /enkube

ENTRYPOINT ["enkube"]
