# Blue Proxy Manager — Multi-node control plane (beta)

The original single-server inbound, user and Telegram bot pages continue to
manage the central VPS. The new /nodes page manages remote nodes and their
independent inbounds, users, domains, geographic locations and Secret rotation.

## Architecture

Controller -> desired state stored in nodes.json -> outbound authenticated
HTTPS polling from each node -> validated root reconciler -> MTProxy, WEB Relay
and Caddy. The node reports its applied revision through HTTPS. There is no
remote SSH password, inbound agent port or arbitrary-command control channel.

Each node has a unique 36-byte random bearer credential, shown only once at
registration. The central controller stores its SHA-256 digest and nodes use
a root-readable-only /etc/blue-mtproxy/node.json. Treat this credential as
sensitive: anyone holding it can retrieve the node's proxy credentials.

## One-time onboarding

1. Install the existing Blue Proxy VPS application once on a fresh Debian
   or Ubuntu x86_64 server using vps/bootstrap.sh. Set a proxy hostname and
   a separate panel hostname. Initial bootstrapping needs root access once;
   completely login-free initial deployment requires a cloud provider API
   or cloud-init integration that is not available in this release.
2. In the central panel open /nodes and register name, location and domain.
   Copy the one-time pairing token; it will not be displayed again.
3. On the newly installed VPS (with this software release), run:

       sudo bash /opt/blue-mtproxy/vps/install-node.sh \
         --controller https://panel.example.com --node-id nYOUR_NODE_ID

   Paste the token at the hidden prompt. The node's separate admin panel
   and Telegram publishing bot are disabled; central management remains.
4. Create the first inbound in /nodes. The agent polls every ~minute. View
   desired/applied revision in the node detail page.
5. Add aliases, bind a domain to each inbound, create and revoke users, rotate
   Secrets or move an inbound to a different provisioned node. After moving,
   use the new domain in the inbound URLs.

## TLS and Cloudflare

The node manages a separate Caddy include file at
/etc/caddy/blue-mtproxy-domains.caddy and reloads Caddy only after validation.
Caddy obtains and renews ACME HTTPS certificates automatically after DNS
points to the node public IP and TCP 80/443 is reachable.

- DNS records are not created automatically; configure A/AAAA records with
  your DNS provider and do not publish an unreachable AAAA record.
- For Cloudflare Proxied HTTPS, use Full (strict). The WEB relay requires a
  WEB-capable Telegram client; a stock Telegram MTProto client does not
  support this experimental carrier.
- Direct native MTProto links require public TCP 10443+slot and do not pass
  through ordinary Cloudflare HTTP CDN. CDN-only nodes intentionally block
  these ports.
- A successful Caddy reload does NOT prove issuance or DNS propagation.
  Verify TLS with normal public client certificate validation.

## Diagnostics

On a remote node:

    sudo systemctl status blue-mtproxy-node.timer --no-pager
    sudo systemctl start blue-mtproxy-node.service
    sudo journalctl -u blue-mtproxy-node.service -n 60 --no-pager
    sudo journalctl -u caddy.service -n 50 --no-pager

Only the fixed desired-state API is available to node tokens:
GET /api/nodes/<id>/desired and POST /api/nodes/<id>/report.

## Current boundaries

Remote inbounds are managed in /nodes, separate from the central VPS
/inbounds list. A remote node retains its bootstrap state until its first
managed inbound is assigned; deleting its last managed inbound is blocked.
The upstream WEB relay's maximum of 32 active Secrets applies separately
to each node. Per-user bytes or connections are not measured by the stock
MTProxy backend and must not be represented as verified statistics.
Moving an inbound can temporarily leave its secret active on both source
and destination until both agents reconcile.
