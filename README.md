# Enkube

Enkube (rhymes with "enqueue") is a toolkit for managing and deploying things
into Kubernetes clusters. The main goal of enkube is to reduce the amount of
pain felt by cluster operators. Jsonnet templates are used to reduce
boilerplate and enable composition in manifest files.

## Getting Started

First, follow the instructions in [the Installation section](#installation) to
get up and running quickly. Once enkube is installed, you can [render your first
manifest file](#render-your-first-manifest-file).

## Installation

Perhaps the easiest way to run enkube is with Docker:

```
$ docker run --rm -it spideroak/enkube
```

If you'd like to install enkube yourself (say, if you're on a Mac), then you'll
need a couple of things first:

* Python 3.7
* kubectl

For Python, we recommend using pyenv:

```
$ # install pyenv and friends
$ curl -L https://github.com/pyenv/pyenv-installer/raw/master/bin/pyenv-installer | bash

$ # install python 3.7 into its own environment
$ pyenv install 3.7.0

$ # create and activate a virtual environment for enkube, and update pip
$ pyenv virtualenv 3.7.0 enkube
$ pyenv shell enkube
$ pip install -U pip
```

In order to interact with a Kubernetes cluster, you will also need to
[install kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl/#install-kubectl-binary-using-curl):

```
$ curl -LO https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/darwin/amd64/kubectl
$ chmod +x ./kubectl
$ sudo mv ./kubectl /usr/local/bin/kubectl
```

Finally, clone and install Enkube:

```
# clone and install enkube
$ git clone https://github.com/SpiderOak/enkube.git
$ cd enkube
$ pip install -r requirements.txt
$ pip install -e .
```

## Render Your First Manifest File

Let's create a simple manifest file that defines a single Kubernetes Pod:

```jsonc
/* pod.jsonnet */
local k = import "enkube/k";
k.Pod("myapp", [
  k.Container("myapp", "myregistry/myapp:latest")
]).ns("default")
```

Now tell enkube to render the manifest into YAML suitable for applying to a
cluster:

```
$ enkube render pod.jsonnet
---
# File: pod.jsonnet
apiVersion: v1
kind: Pod
metadata:
  name: myapp
  namespace: default
spec:
  containers:
  - image: myregistry/myapp:latest
    name: myapp
```

If you have access to a running Kubernetes cluster (and your kubeconfig file is
in a default location), you can apply your Pod to the cluster:

```
$ enkube apply pod.jsonnet
pod/myapp created
$ enkube ctl get pod
NAME                      READY     STATUS              RESTARTS   AGE
myapp                     0/1       ContainerCreating   0          5s
```

## Environments and Interacting with Kubernetes

Enkube makes it easy to interact with one or many Kubernetes clusters. A good
practice is to organize your manifests into projects according to your
workload. For example, say you want to deploy a simple web app. You might have
a few files that define Kubernetes resources for various components to be
deployed. Group these together under a `manifests` directory under your
top-level project:

```
mywebapp/
- manifests/
  - ingress.jsonnet
  - pod.jsonnet
```

You can then render and/or apply all of these manifests with a single command:

```
$ enkube render
---
# File: manifests/ingress.jsonnet
...
---
# File: manifests/pod.jsonnet
...
```

By default, enkube looks for manifests to render under the `manifests`
directory, but you can pass it the path to any file or directory if you want to
be explicit. When given a directory, enkube will recursively descend into
subdirectories and render all manifests it finds.

### Interacting with Multiple Clusters

With the above organizational structure, enkube makes it easy to manage
multiple clusters running similar workloads. Usually it's a good idea to run
one cluster for the production deployment of your app, and a separate cluster
for development or staging. For the most part, the workloads running on these
two clusters are the same, perhaps with some minor differences in
configuration. This is where enkube environments and template variables can
help.

In order to use environments, just create an `envs` directory in your project
heirarchy, and put your kubeconfig files in subdirectories thereof for each
environment:

```
mywebapp/
- envs/
  - prod/
    - .kubeconfig
  - staging/
    - .kubeconfig
- manifests/
  - ingress.jsonnet
  - pod.jsonnet
```

Now, to interact with a particular cluster, simply pass the environment name to
enkube. This will automatically pass the correct kubeconfig file to kubectl
under the hood:

```
$ enkube -e staging ctl get nodes
NAME                         STATUS    ROLES               AGE       VERSION
master1.staging.example.com  Ready     controller,master   69d       v1.10.5
...
```

```
$ enkube -e prod ctl get nodes
NAME                         STATUS    ROLES               AGE       VERSION
master1.prod.example.com     Ready     controller,master   69d       v1.10.5
...
```

### Jsonnet Imports, the Search Path, and Environments

The power of templating lies in variables, and variables should be able to vary
from one environment to another. We accomplish this using jsonnet variables and
the `import` statement to compose values together from multiple template files.

Say our web app needs to know the URL it is available at. This URL is different
in production and staging, so we need to provide this to the app using an
environment variable. Let's create a `config.libsonnet` file for each
environment that defines this variable:

```jsonc
/* envs/staging/config.libsonnet */
{
  external_url: "myapp-staging.example.com"
}
```

```jsonc
/* envs/prod/config.libsonnet */
{
  external_url: "myapp.example.com"
}
```

Now, we have the following heirarchy:

```
mywebapp/
- envs/
  - prod/
    - .kubeconfig
    - config.libsonnet
  - staging/
    - .kubeconfig
    - config.libsonnet
- manifests/
  - ingress.jsonnet
  - pod.jsonnet
```

We will also need to update our Pod definition to pass this variable to our
container as an environment variable. It might look something like this:

```jsonc
/* manifests/pod.jsonnet */
local k = import "enkube/k";
local config = import "config";
k.Pod("myapp", [
  k.Container("myapp", "myregistry/myapp:latest").env_({
    EXTERNAL_URL: config.external_url
  })
]).ns("default")
```

The way this works, is that any time enkube comes across a jsonnet `import`
statement, it adds the current environment to the search path. So when jsonnet
looks for `config.libsonnet`, it will first look in the current environment,
where it will find the file we just created. Also note that when importing,
you can leave off the `.libsonnet` extension.

Let's render the Pod template for each environment and see what happens:

```
$ enkube -e staging render manifests/pod.jsonnet
---
# File: manifests/pod.jsonnet
apiVersion: v1
kind: Pod
metadata:
  name: myapp
  namespace: default
spec:
  containers:
  - env:
    - name: EXTERNAL_URL
      value: myapp-staging.example.com
    image: myregistry/myapp:latest
    name: myapp
```

```
$ enkube -e prod render manifests/pod.jsonnet
---
# File: manifests/pod.jsonnet
apiVersion: v1
kind: Pod
metadata:
  name: myapp
  namespace: default
spec:
  containers:
  - env:
    - name: EXTERNAL_URL
      value: myapp.example.com
    image: myregistry/myapp:latest
    name: myapp
```

Environments, variables, and the import search path are the core principles on
which enkube rendering is based. With these simple tools, and a carefully
organized project, enkube helps bring order and stability to complex
deployments.

## Showing Differences Between Local Manifests and Running Cluster

As your app changes over time, it will become necessary to modify the manifest
files that define your workload, and apply those changes to your clusters. It
can be handy to compare the output of your rendered templates against a running
cluster to see what would change if you were to apply it. Enkube makes this
easy with the `diff` command. First, let's change the URL to our example web
app:

```jsonc
/* envs/staging/config.libsonnet */
{
  external_url: "coolapp-staging.example.com"
}
```

We can show what would change if we applied this to our staging cluster by
running `enkube diff`:

```diff
$ enkube -e staging diff manifests/pod.jsonnet
Changed Pod default/myapp
--- default/myapp CLUSTER
+++ default/myapp LOCAL
@@ -9,6 +9,6 @@
   containers:
   - env:
     - name: EXTERNAL_URL
-      value: myapp-staging.example.com
+      value: coolapp-staging.example.com
     image: myregistry/myapp:latest
     name: myapp
```

## Interacting with the Kubernetes API

## Writing a Kubernetes Controller Using Enkube

## Enkube is Built With

* [click](https://click.palletsprojects.com/en/7.x/) - Command line interface
* [jsonnet](https://jsonnet.org/) - Data structure templating
* [jinja2](http://jinja.pocoo.org/) - Flat file templating
* [deepdiff](https://pypi.org/project/deepdiff/) - Calculating diffs between objects
* [curio](https://github.com/dabeaz/curio) - Async IO
* [asks](https://github.com/theelous3/asks) - Async HTTP requests
* [requests](http://docs.python-requests.org/en/master/) - Syncronous HTTP requests
* [pyaml](https://pypi.org/project/pyaml/) - Generating friendly YAML output
* [PyYAML](https://pyyaml.org/) - Parsing YAML
* [pygments](http://pygments.org/) - Syntax highlighting

## Authors / Contributors

* **Sadie Hain** - *Initial work* - [dhain](https://github.com/dhain)
* **Robert Fairburn** - *Contributor* - [rfairburn](https://github.com/rfairburn)
* **Josh Reichardt** - *Contributor* - [jmreicha](https://github.com/jmreicha)

## License

This project is licensed under the
[Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0).

## Acknowledgments

This project would not be possible without [Jsonnet](https://jsonnet.org/).
Inspiration has also been drawn from [Ansible](https://www.ansible.com/).
