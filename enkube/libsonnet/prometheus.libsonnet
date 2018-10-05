/* vim:ts=2 sw=2

Copyright 2018 SpiderOak, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.


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

    Optional arguments:
      alertManager: The alertmanager instance to connect to.
  */
  Prometheus(name, url, serviceMonitorSelector, alertManagers=null, replicas=1)::
    k._Object("monitoring.coreos.com/v1", "Prometheus", name).labels({
      "k8s-app": "prometheus",
      prometheus: name,
    }) {
      spec: {
        replicas: replicas,
        alerting: { alertmanagers: if alertManagers != null then alertManagers else [
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
    k._Object("monitoring.coreos.com/v1", "Alertmanager", name) { spec: {
      [if url != null then "externalUrl"]: url,
      replicas: replicas,
    } },

  ServiceMonitor(name, matchNamespaces, matchLabels, endpoints, jobLabel=null)::
    k._Object("monitoring.coreos.com/v1", "ServiceMonitor", name) { spec: {
      namespaceSelector: { matchNames: matchNamespaces },
      selector: { matchLabels: matchLabels },
      [if jobLabel != null then "jobLabel"]: jobLabel,
      endpoints: endpoints,
    } },

  PrometheusRule(name, spec, labels)::
    k._Object("monitoring.coreos.com/v1", "PrometheusRule", name).labels(labels) { spec: spec },
}
