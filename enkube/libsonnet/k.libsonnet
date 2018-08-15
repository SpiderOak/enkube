/* vim:ts=2 sw=2

Kubernetes object prototypes

*/
{
  _Object(apiVersion, kind, name=null):: {
    local s = self,
    apiVersion: apiVersion,
    kind: kind,
    [if name != null then "metadata"]: { name: name },
    name(name):: self + { metadata+: { name: name } },
    ns(ns):: self + { metadata+: { namespace: ns } },
    labels(labels):: self + { metadata+: { labels: labels } },
    ref:: {
      apiVersion: s.apiVersion,
      kind: s.kind,
      metadata: {
        name: s.metadata.name,
        namespace: s.metadata.namespace,
      },
    },
  },

  ClusterScoped:: { ns(ns):: self },

  applyNamespace(ns, items):: std.map(
    function(i) if std.objectHasAll(i, "ns") then i.ns(ns) else i, items
  ),

  /*
    List of Kubernetes resources

    Required arguments:
      items: A list of Kubernetes resources.
  */
  List(items):: $._Object("v1", "List") {
    items: std.filter(function(i) i != null, items),
    map(f):: self + { items: std.map(f, super.items) },
    ns(ns):: self + { items: $.applyNamespace(ns, super.items) },
  },

  /*
    Namespace

    Required arguments:
      name: The namespace name.
  */
  Namespace(name):: $._Object("v1", "Namespace", name) + $.ClusterScoped,

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
  Role(name, rules):: $._Object("rbac.authorization.k8s.io/v1", "Role", name) { rules: rules },

  /*
    ClusterRole

    Required arguments:
      name: The name of the ClusterRole.
      rules: A list of PolicyRules.
  */
  ClusterRole(name, rules):: $.Role(name, rules) + $.ClusterScoped + {
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
      role: The name of the Role to bind to.
      subjects: A list of subjects to bind to the Role.
  */
  RoleBinding(name, role, subjects)::
    $._Object("rbac.authorization.k8s.io/v1", "RoleBinding", name) {
      roleRef: {
        apiGroup: "rbac.authorization.k8s.io",
        kind: "Role",
        name: role,
      },
      subjects: subjects,
    },

  /*
    ClusterRoleBinding

    Required arguments:
      name: The ClusterRoleBinding name.
      role: The name of the ClusterRole to bind to.
      subjects: A list of subjects to bind to the ClusterRole.
  */
  ClusterRoleBinding(name, role, subjects)::
    $.RoleBinding(name, role, subjects) + $.ClusterScoped + {
      kind: "ClusterRoleBinding",
      roleRef+: { kind: "ClusterRole" },
    },

  IPBlock(cidr, except=null):: { cidr: cidr, [if except != null then "except"]: except },

  /*
    NetworkPolicy

    Required arguments:
      name: The name of the NetworkPolicy object.
      selector: The pod selector to associate with the NetworkPolicy.
  */
  NetworkPolicy(name, selector)::
    $._Object("networking.k8s.io/v1", "NetworkPolicy", name) {
      local s = self,
      spec: {
        podSelector: { matchLabels: selector },
        policyTypes: std.prune([
          if std.objectHas(self, "ingress") then "Ingress",
          if std.objectHas(self, "egress") then "Egress",
        ]),
      },
      ingress(rule):: self + { spec+: {
        local i = if std.objectHas(s.spec, "ingress") then s.spec.ingress else [],
        ingress: i + [rule { from: rule.peers }],
      } },
    },

  NetworkPolicyRule(peers):: {
    local s = self,
    peers:: peers,
    ports: [],
    port(port, proto="TCP"):: self + { ports: s.ports + [{
      port: port,
      protocol: proto,
    }] },
  },

  /*
    Endpoints

    Required arguments:
      name: The name of the Endpoints object.
      subsets: A list of EndpointSubsets.
  */
  Endpoints(name, subsets):: $._Object("v1", "Endpoints", name) { subsets: subsets },

  EndpointSubset(ips):: {
    local s = self,
    addresses: [{ ip: i } for i in ips],
    ports: [],
    port(port, name=null, proto=null):: self + { ports: s.ports + [{
      port: port,
      [if name != null then "name"]: name,
      [if proto != null then "protocol"]: proto,
    }] },
  },

  /*
    Service

    Required arguments:
      name: The name of the service.

    Optional arguments:
      selector: The pod selector to associate with the service.
  */
  Service(name, selector=null):: $._Object("v1", "Service", name) {
    local s = self,
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
      spec+: {
        local p = if std.objectHas(s.spec, "ports") then s.spec.ports else [],
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
    local c = self,
    name: name,
    image: image,

    /*
      Container env helper
    */
    env_(env):: self + { env: [
      { name: k, value: env[k] }
      for k in std.objectFields(env)
    ] },

    /*
      Container port helper

      Adds a port to the container. Can be chained to add multiple ports to the
      same container.

      Required arguments:
        port: The port number.

      Optional arguments:
        name: The name of the port.
    */
    port(port, name=null):: self + {
      local p = if std.objectHas(c, "ports") then c.ports else [],
      ports: p + [{
        containerPort: port,
        [if name != null then "name"]: name,
      }],
    },
  },

  /*
    Pod

    Required arguments:
      name: The name of the pod.
      containers: A list of Containers.

    Optional arguments:
      nodeSelector: Selector to limit which nodes pod will run on.
      initContainers:  Containers run during pod initialization.
  */
  Pod(name, containers, nodeSelector=null, initContainers=null)::
    $._Object("v1", "Pod", name) +
    $._PodSpec(containers, nodeSelector),

  _PodSpec(containers, nodeSelector=null, initContainers=null):: {
    local s = self,
    spec: {
      containers: containers,
      [if nodeSelector != null then "nodeSelector"]: nodeSelector,
      [if initContainers != null then "initContainers"]: initContainers,
    },
    serviceAccountName(name):: self + { spec+: { serviceAccountName: name } },
    imagePullSecret(name):: self + { spec+: {
      local i = if std.objectHas(s.spec, "imagePullSecrets") then s.spec.imagePullSecrets else [],
      imagePullSecrets: i + [{ name: name }],
    } },
    securityContext(ctx):: self + { spec+: { securityContext: ctx } },
    volume(vol):: self + { spec+: {
      local v = if std.objectHas(s.spec, "volumes") then s.spec.volumes else [],
      volumes: v + [vol],
    } },
    tolerateMasters():: self + { spec+: {
      local t = if std.objectHas(s.spec, "tolerations") then s.spec.tolerations else [],
      tolerations: t + [
        { key: "node-role.kubernetes.io/master", operator: "Exists", effect: "NoSchedule" },
      ],
    } },
  },

  _PodSpecTemplate(labels, containers, nodeSelector=null, initContainers=null):: {
    local t = self.spec.template,
    spec: {
      selector: { matchLabels: labels },
      template: $._PodSpec(containers, nodeSelector, initContainers) {
        metadata: { labels: labels },
      },
    },
    serviceAccountName(name):: self + { spec+: { template: t.serviceAccountName(name) } },
    imagePullSecret(name):: self + { spec+: { template: t.imagePullSecret(name) } },
    securityContext(ctx):: self + { spec+: { template: t.securityContext(ctx) } },
    volume(vol):: self + { spec+: { template: t.volume(vol) } },
    tolerateMasters():: self + { spec+: { template: t.tolerateMasters() } },
  },

  /*
    Deployment

    Required arguments:
      name: The name of the deployment.
      labels: An object specifying labels to match Pods.
      containers: A list of Containers.

    Optional arguments:
      initContainers:  Containers run during pod initialization.
  */
  Deployment(name, labels, containers, initContainers=null)::
    $._Object("apps/v1", "Deployment", name).labels(labels) +
    $._PodSpecTemplate(labels, containers, initContainers=initContainers ),

  /*
    DaemonSet

    Required arguments:
      name: The name of the daemonset.
      labels: An object specifying labels to match Pods.
      containers: A list of Containers.

    Optional arguments:
      nodeSelector: An object specifying which nodes to run Pods on.
      initContainers:  Containers run during pod initialization.
  */
  DaemonSet(name, labels, containers, nodeSelector=null, initContainers=null)::
    $._Object("apps/v1", "DaemonSet", name).labels(labels) +
    $._PodSpecTemplate(labels, containers, nodeSelector, initContainers),

  /*
    StatefulSet

    Required arguments:
      name: The name of the stateful set.
      serviceName: Name of the Service controlling the stateful set.
      labels: An object specifying labels to match Pods.
      containers: A list of Containers.

    Optional arguments:
      initContainers:  Containers run during pod initialization.
  */
  StatefulSet(name, serviceName, labels, containers, initContainers=null)::
    $._Object("apps/v1", "StatefulSet", name).labels(labels) +
    $._PodSpecTemplate(labels, containers, initContainers=initContainers) { spec+: { serviceName: serviceName } },

  /*
    Job

    Required arguments:
      name: The name of the job.
      containers: A list of Containers.
  */
  Job(name, containers)::
    $._Object("batch/v1", "Job", name) {
      local t = self.spec.template,
      spec: { template: $._PodSpec(containers) {
        spec+: { restartPolicy: "Never" },
      } },
      serviceAccountName(name):: self + { spec+: { template: t.serviceAccountName(name) } },
      imagePullSecret(name):: self + { spec+: { template: t.imagePullSecret(name) } },
      securityContext(ctx):: self + { spec+: { template: t.securityContext(ctx) } },
      volume(vol):: self + { spec+: { template: t.volume(vol) } },
      tolerateMasters():: self + { spec+: { template: t.tolerateMasters() } },
    },

  /*
    CronJob

    Required arguments:
      name: The name of the cron job.
      schedule: A string in cron format specifying the job schedule.
      containers: A list of Containers.

    Optional arguments:
      concurrencyPolicy: Specifies how to treat concurrent executions of a Job.
  */
  CronJob(name, schedule, containers, concurrencyPolicy=null)::
    $._Object("batch/v1beta1", "CronJob", name) {
      local t = self.spec.jobTemplate.spec.template,
      spec: {
        schedule: schedule,
        [if concurrencyPolicy != null then "concurrencyPolicy"]: concurrencyPolicy,
        jobTemplate: { spec: { template: $._PodSpec(containers) {
          spec+: { restartPolicy: "OnFailure" },
        } } },
      },
      serviceAccountName(name):: self + { spec+: { jobTemplate+: {
        spec+: { template: t.serviceAccountName(name) },
      } } },
      imagePullSecret(name):: self + { spec+: { jobTemplate+: {
        spec+: { template: t.imagePullSecret(name) },
      } } },
      securityContext(ctx):: self + { spec+: { jobTemplate+: {
        spec+: { template: t.securityContext(ctx) },
      } } },
      volume(vol):: self + { spec+: { jobTemplate+: {
        spec+: { template: t.volume(vol) },
      } } },
      tolerateMasters():: self + { spec+: { jobTemplate+: {
        spec+: { template: t.tolerateMasters() },
      } } },
    },

  /*
    StorageClass

    Required arguments:
      name: The name of the StorageClass.
  */
  StorageClass(name):: $._Object("storage.k8s.io/v1", "StorageClass", name) + $.ClusterScoped,

  /*
    Local StorageClass
  */
  LocalStorageClass():: $.StorageClass("local-storage") {
    provisioner: "kubernetes.io/no-provisioner",
    volumeBindingMode: "WaitForFirstConsumer",
  },

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
    $._Object("v1", "PersistentVolume", name).labels({ name: name, type: "local" }) + $.ClusterScoped + {
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
    TLS Secret

    Required arguments:
      name: The name of the secret.
      cert: The certificate in PEM format.
      key: The private key in PEM format.
  */
  TLSSecret(name, cert, key):: $.Secret(name, {
    "tls.crt": cert,
    "tls.key": key,
  }, "kubernetes.io/tls"),

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
    local s = self,
    backend_(serviceName, port):: self + $._IngressBackend(serviceName, port),
    [if rules != null then "rules"]: rules,
    tls_(secretName, hosts=null):: self + { tls: [{
      hosts: if hosts == null then [r.host for r in s.rules] else hosts,
      secretName: secretName,
    }] },
  },

  /*
    IngressRule

    Required arguments:
      host: The DNS hostname the rule applies to.
  */
  IngressRule(host):: {
    local r = self,
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
      http+: { paths: r.http.paths + [
        $._IngressBackend(serviceName, port) + if path == null then {} else { path: path },
      ] },
    },
  },

  /*
    HostIngress Helper

    Gives you a nicer interface for creating Ingresses.

    Required arguments:
      name: The name of the Ingress.
      hostname: The hostname the Ingress applies to.
      class: The ingress class to use in the annotation.

    Optional arguments:
      tlsSecretName: Name of the secret containing TLS certificates.
      annotations: Additional annotations to add.

    Methods:
      path: Add a backend to the Ingress at a given URL path.
  */
  HostIngress(name, hostname, class, tlsSecretName=null, annotations={})::
    $.Ingress(name, class, $.IngressSpec([$.IngressRule(hostname)]) {
      [if tlsSecretName != null then "tls"]: [{
        secretName: tlsSecretName,
      }],
    }, annotations) {
      local s = self,
      path(path, serviceName, port):: self + { spec+: { rules: [
        s.spec.rules[0].backend(serviceName, port, path),
      ] } },
      _domain:: self.spec.rules[0].host,
    },
}
