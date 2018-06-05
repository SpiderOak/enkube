/* vim:ts=2 sw=2

Kubernetes object prototypes

*/
{
  _Object(apiVersion, kind, name=null):: {
    apiVersion: apiVersion,
    kind: kind,
    [if name != null then "metadata"]: { name: name },
    ns(ns):: self + { metadata+: { namespace: ns } },
    labels(labels):: self + { metadata+: { labels: labels } },
  },

  _NoNamespace:: { ns(ns):: self },

  /*
    List of Kubernetes resources

    Required arguments:
      items: A list of Kubernetes resources.
  */
  List(items):: $._Object("v1", "List") {
    items: items,
    map(f):: self + { items: std.map(f, super.items) },
    ns(ns):: self.map(function(i) if std.objectHas(i, "ns") then i.ns(ns) else i),
  },

  /*
    Namespace

    Required arguments:
      name: The namespace name.
  */
  Namespace(name):: $._Object("v1", "Namespace", name) + $._NoNamespace,

  /*
    ServiceAccount

    Required arguments:
      name: The name of the ServiceAccount.
  */
  ServiceAccount(name):: $._Object("v1", "ServiceAccount", name),

  /*
    ConfigMap

    Required arguments:
      name: The name of the ConfigMap.
      data: The ConfigMap data.
  */
  ConfigMap(name, data):: $._Object("v1", "ConfigMap", name) { data: data },

  /*
    Role

    Required arguments:
      name: The name of the Role.
      rules: A list of PolicyRules.
  */
  Role(name, rules):: $._Object("rbac.authorization.k8s.io/v1", "Role", name) {
    rules: rules,
    roleRef:: {
      apiGroup: std.splitLimit(self.apiVersion, "/", 1)[0],
      kind: self.kind,
      name: self.name,
    },
  },

  /*
    ClusterRole

    Required arguments:
      name: The name of the ClusterRole.
      rules: A list of PolicyRules.
  */
  ClusterRole(name, rules):: $.Role(name, rules) + $._NoNamespace + {
    kind: "ClusterRole",
  },

  /*
    PolicyRule
  */
  PolicyRule(apiGroups, resources, verbs):: {
    apiGroups: apiGroups,
    resources: resources,
    verbs: verbs,
  },

  /*
    RoleBinding

    Required arguments:
      name: The RoleBinding name.
      role: The Role object to bind to.
      subjects: A list of subjects to bind to the Role.
  */
  RoleBinding(name, role, subjects)::
    $._Object("rbac.authorization.k8s.io/v1", "RoleBinding", name) {
      roleRef: role.roleRef,
      subjects: subjects,
    },

  /*
    ClusterRoleBinding

    Required arguments:
      name: The ClusterRoleBinding name.
      role: The ClusterRole object to bind to.
      subjects: A list of subjects to bind to the ClusterRole.
  */
  ClusterRoleBinding(name, role, subjects)::
    $.RoleBinding(name, role, subjects) + $._NoNamespace + {
      kind: "ClusterRoleBinding",
    },

  /*
    Service

    Required arguments:
      name: The name of the service.

    Optional arguments:
      selector: The pod selector to associate with the service.
  */
  Service(name, selector=null):: $._Object("v1", "Service", name) {
    spec: {
      [if selector != null then "selector"]: selector,
    },

    noClusterIP():: self + { spec+: { clusterIP: "None" } },
    externalIPs(ips):: self + { spec+: { externalIPs: ips } },
    nodePort():: self + { spec+: { type: "NodePort" } },

    externalName(name):: self + { spec+: {
      type: "ExternalName",
      externalName: name,
    } },

    loadBalancer(ip=null):: self + { spec+: {
      type: "LoadBalancer",
      [if ip != null then "loadBalancerIP"]: ip,
    } },

    /*
      Service port helper

      Adds a port to the service. Can be chained to add multiple ports to the
      same service.

      Required arguments:
        port: The port number.

      Optional arguments:
        name: The name of the port.
        proto: The protocol. Default is "TCP"
        nodePort: The node port number for NodePort or LoadBalancer services.
        targetPort: The target port number.
    */
    port(port, name=null, proto="TCP", nodePort=null, targetPort=null):: self + {
      local p = if std.objectHas(super.spec, "ports") then super.spec.ports else [],
      spec+: {
        ports: p + [{
          protocol: proto,
          port: port,
          [if name != null then "name"]: name,
          [if targetPort != null then "targetPort"]: targetPort,
          [if nodePort != null then "nodePort"]: nodePort,
        }],
      },
    },
  },

  /*
    Container

    Required arguments:
      name: The name of the container.
      image: The image to run the container from.
  */
  Container(name, image):: {
    name: name,
    image: image,
    env(env):: self + { environ: [
      { name: k, value: env[k] }
      for k in std.objectFields(env)
    ] },
    ports_(ports):: self + { ports: [
      if std.isNumber(p) then { containerPort: p } else p
      for p in ports
    ] },
  },

  _PodSpecTemplate(labels, containers, nodeSelector=null):: {
    spec: {
      selector: { matchLabels: labels },
      template: {
        metadata: { labels: labels },
        spec: {
          containers: containers,
          [if nodeSelector != null then "nodeSelector"]: nodeSelector,
        },
      },
    },
  },

  /*
    Deployment

    Required arguments:
      name: The name of the deployment.
      labels: An object specifying labels to match Pods.
      containers: A list of Containers.
  */
  Deployment(name, labels, containers)::
    $._Object("apps/v1", "Deployment", name).labels(labels) +
    $._PodSpecTemplate(labels, containers),

  /*
    DaemonSet

    Required arguments:
      name: The name of the daemonset.
      labels: An object specifying labels to match Pods.
      containers: A list of Containers.

    Optional arguments:
      nodeSelector: An object specifying which nodes to run Pods on.
  */
  DaemonSet(name, labels, containers, nodeSelector=null)::
    $._Object("apps/v1", "DaemonSet", name).labels(labels) +
    $._PodSpecTemplate(labels, containers, nodeSelector),

  /*
    StatefulSet

    Required arguments:
      name: The name of the stateful set.
      serviceName: Name of the Service controlling the stateful set.
      labels: An object specifying labels to match Pods.
      containers: A list of Containers.
  */
  StatefulSet(name, serviceName, labels, containers)::
    $._Object("apps/v1", "StatefulSet", name).labels(labels) +
    $._PodSpecTemplate(labels, containers) { spec+: { serviceName: serviceName } },

  /*
    Local PersistentVolume

    Required arguments:
      name: The name of the PersistentVolume.
      node: The node with the storage.
      path: The filesystem path on the node to bind.
      capacity: The capacity of the volume.

    Optional arguments:
      accessModes: A list of access modes. Defaults to ReadWriteOnce.
  */
  LocalPersistentVolume(name, node, path, capacity, accessModes=null)::
    $._Object("v1", "PersistentVolume", name).labels({ name: name, type: "local" }) + $._NoNamespace + {
      spec: {
        storageClassName: "local-storage",
        capacity: { storage: capacity },
        accessModes: if accessModes == null then ["ReadWriteOnce"] else accessModes,
        hostPath: { path: path },
        nodeAffinity: { required: { nodeSelectorTerms: [{ matchExpressions: [
          { key: "kubernetes.io/hostname", operator: "In", values: [node] },
        ] }] } },
      },
    },

  /*
    Local PersistentVolumeClaim

    Required arguments:
      name: The name of the PersistentVolumeClaim.
      storageRequest: The amount of storage to request.

    Optional arguments:
      accessModes: A list of access modes. Defaults to ReadWriteOnce.
  */
  LocalPersistentVolumeClaim(name, storageRequest, accessModes=null)::
    $._Object("v1", "PersistentVolumeClaim", name) {
      spec: {
        storageClassName: "local-storage",
        resources: { requests: { storage: storageRequest } },
        accessModes: if accessModes == null then ["ReadWriteOnce"] else accessModes,
        selector: { matchLabels: { type: "local", name: name } },
      },
    },

  /*
    Secret

    Required arguments:
      name: The name of the secret.
      data: The secret data. Values will become base64-encoded.

    Optional arguments:
      type: The type of secret. Default is "Opaque".
  */
  Secret(name, data, type="Opaque"):: $._Object("v1", "Secret", name) {
    type: type,
    data: { [k]: std.base64(data[k]) for k in std.objectFields(data) },
  },

  /*
    Docker Registry Secret

    Required arguments:
      name: The name of the secret.
      imagePullSecrets: Map of registry name -> credentials.

    Optional arguments:
      registries: A list of registry names to include in the secret.
  */
  RegistrySecret(name, imagePullSecrets, registries=null):: $.Secret(name, {
    ".dockerconfigjson": std.manifestJsonEx({ auths: {
      [r]: { auth: std.base64(imagePullSecrets[r].username + ":" + imagePullSecrets[r].password) }
      for r in if registries == null then std.objectFields(imagePullSecrets) else registries
    } }, " "),
  }, "kubernetes.io/dockerconfigjson"),

  /*
    Ingress

    Required arguments:
      name: The name of the ingress.
      class: The ingress class to use in the annotation.
      spec: The IngressSpec for this ingress.

    Optional arguments:
      annotations: Additional annotations to add.
  */
  Ingress(name, class, spec, annotations={}):: $._Object("extensions/v1beta1", "Ingress", name) {
    metadata+: { annotations: { "kubernetes.io/ingress.class": class } + annotations },
    spec: spec,
    assert std.objectHas(self.spec, "backend") || std.objectHas(self.spec, "rules") :
           "Ingress must specify at least one of backend or rules",
  },

  _IngressBackend(serviceName, port):: { backend: { serviceName: serviceName, servicePort: port } },

  /*
    IngressSpec

    Optional arguments:
      rules: A list of IngressRules.
  */
  IngressSpec(rules=null):: {
    backend_(serviceName, port):: self + $._IngressBackend(serviceName, port),
    [if rules != null then "rules"]: rules,
  },

  /*
    IngressRule

    Required arguments:
      host: The DNS hostname the rule applies to.
  */
  IngressRule(host):: {
    host: host,
    http: { paths: [] },

    /*
      IngressRule backend helper

      Adds a backend service to a rule, with an optional URL path. Can be
      chained to add multiple backends to the same rule.

      Required arguments:
        serviceName: The name of the service to send requests to.
        port: The port name or number to send requests to.

      Optional arguments:
        path: The URL path to "mount" the backend to.
    */
    backend(serviceName, port, path=null):: self + {
      local h = super.http,
      http+: { paths: h.paths + [
        $._IngressBackend(serviceName, port) + if path == null then {} else { path: path },
      ] },
    },
  },
}
