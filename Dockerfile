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
