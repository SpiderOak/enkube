/* vim:ts=2 sw=2

Prometheus Operator object prototypes

*/
local k = import "enkube/k";

{
  /*
    Prometheus

    Required arguments:
      name: The name of the prometheus instance.
      url: The external URL of the prometheus interface.
      serviceMonitorSelector: Selector used to match Services to monitor.
  */
  Prometheus(name, url, serviceMonitorSelector)::
    k._Object("monitoring.coreos.com/v1", "Prometheus", name).labels({
      "k8s-app": "prometheus",
      prometheus: name,
    }) {
      spec: {
        alerting: { alertmanagers: [
          { name: "alertmanager-main", namespace: "kube-system", port: "http" },
        ] },
        [if url != null then "externalUrl"]: url,
        resources: { requests: { memory: "400Mi" } },
        ruleSelector: { matchLabels: { prometheus: name, role: "prometheus-rulefiles" } },
        serviceAccountName: "prometheus",
        serviceMonitorSelector: serviceMonitorSelector,
      },
    },

  /*
    Alertmanager

    Required arguments:
      name: The name of the alertmanager instance.

    Optional arguments:
      url: The external URL of the alertmanager interface.
      replicas: The desired number of replicas in the cluster.
  */
  Alertmanager(name, url=null, replicas=3)::
    k._Object("monitoring.coreos.com/v1", "Alertmanager", name) {
      spec: {
        [if url != null then "externalUrl"]: url,
        replicas: replicas,
      },
    },
}
