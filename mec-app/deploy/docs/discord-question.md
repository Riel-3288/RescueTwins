Hi ESTIMED/MEC Sandbox team, we are developing RescueTwin for Hackathon #2.

We plan to run two oneM2M MN-CSEs on two MEC hosts, with an edge integration app that maps MEC013 Location and MEC012 RNIS into oneM2M. Our prototype will demonstrate two factory zones and application-layer handover when an agent crosses the boundary.

Could you clarify:

1. My ETSI Labs username is `selinatan05`, and access to the supplied ESTIMED projects is working. Is runtime access to the shared MEC environment provisioned separately, and which host/service endpoints should our team use?
2. Can teams deploy their own Docker images and custom AdvantEDGE scenarios to the shared environment? What registry, Kubernetes namespaces/host access, inbound callback endpoints, and node placement mechanisms are available?
3. Is MEC021 application-state transfer available for team applications, or should the demonstration use application-layer re-registration and label that limitation?
4. Does the shared setup support latency/packet-loss emulation for traffic between our applications, both MN-CSEs, and the IN-CSE, including traffic from external ESP32 nodes?

Thank you. We can share our interface contract and deployment manifests.

---
Prepared draft; no Discord message was posted during this work.
