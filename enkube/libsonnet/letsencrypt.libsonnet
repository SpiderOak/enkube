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


Letsencrypt (certmanager) object prototypes

*/
local k = import "enkube/k";

{
  /*
    Issuer

    Required arguments:
      name: The name of the issuer.
      server: The URL of the issuing server.
      email: The email address of the registering user.
  */
  Issuer(name, server, email):: k._Object("certmanager.k8s.io/v1alpha1", "Issuer", name) {
    spec: {
      acme: {
        server: server,
        email: email,
        privateKeySecretRef: { name: name },
        http01: {},
      },
    },
  },

  /*
    Let's Encrypt Staging Issuer (certmanager)

    Required arguments:
      email: The email address of the registering user.
  */
  LetsEncryptStaging(email):: $.Issuer(
    "letsencrypt-staging", "https://acme-staging-v02.api.letsencrypt.org/directory", email
  ),

  /*
    Let's Encrypt Production Issuer (certmanager)

    Required arguments:
      email: The email address of the registering user.
  */
  LetsEncryptProd(email):: $.Issuer(
    "letsencrypt-prod", "https://acme-v02.api.letsencrypt.org/directory", email
  ),

  /*
    Certificate (certmanager)

    Required arguments:
      name: The name of the certificate.
      secretName: Name of the secret to save the certificate to.
      dnsNames: List of names to include in SAN field of certificate.
      issuer: The name of the Issuer to use.
      ingressClass: The ingress class to use for http challenge endpoint.

    Optional arguments:
      commonName: CN field to use for the certificate. If omitted, uses the
        first item from dnsNames.
  */
  Certificate(name, secretName, dnsNames, issuer, ingressClass, commonName=null)::
    k._Object("certmanager.k8s.io/v1alpha1", "Certificate", name) {
      spec: {
        secretName: secretName,
        issuerRef: {
          name: issuer,
        },
        [if commonName != null then "commonName"]: commonName,
        dnsNames: dnsNames,
        acme: {
          config: [
            {
              http01: { ingressClass: ingressClass },
              domains: dnsNames,
            },
          ],
        },
      },
    },
}
