Processor
m5.large → Intel Xeon Platinum 8175M (Skylake), x86_64
m6g.large → AWS Graviton2, 64-bit ARM Neoverse N1 cores
Both give you 2 vCPUs + 8 GiB RAM, so the compute envelope is identical on paper.
Performance
Graviton2 has a wider issue pipeline and better memory bandwidth per core than Skylake-generation Xeon
In RDS benchmarks (MySQL, PostgreSQL), m6g typically shows 10–20% higher throughput at the same or lower CPU utilization
Single-threaded performance is comparable; m6g wins on multi-threaded and memory-bound workloads
Cost (on-demand, us-east-1)
m5.large → ~$0.192/hr
m6g.large → ~$0.156/hr (~19% cheaper)
Reserved pricing gap is similar. Savings Plans apply to both.
Storage & Network
Both support the same EBS volume types (gp3, io1, io2) and have the same Up to 10 Gbps network baseline on .large. No difference there.
Compatibility
m6g requires an ARM-compatible DB engine build. On RDS this is fully managed — AWS ships Graviton-native builds for MySQL 8.0+, PostgreSQL 12+, MariaDB, and Aurora. You just pick the instance class; no binary-level concern on your end.
