# Support

- **Questions and ideas** — GitHub Discussions (once enabled for this repository) or an
  issue using the *Feature request* template.
- **Bugs** — the *Bug report* template. Include the version, how you deployed (docker
  compose / systemd / local), the login method, and what the control plane logged
  (`journalctl -u skein-control` or `docker compose logs control`).
- **Security issues** — privately, per [SECURITY.md](SECURITY.md). Never in a public issue.
- **Deploying** — start with [docs/deployment.md](docs/deployment.md), then
  [docs/configuration.md](docs/configuration.md) for every switch. The node side is in
  [deploy/pod/README.md](deploy/pod/README.md).

Skein is maintained by volunteers; expect a few days before a reply. There is no paid
support and no hosted service.
