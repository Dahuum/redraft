# Redraft — Power Automate custom connector

One action, **Replace text in a PDF**: it sends a PDF and a list of `{find, replace}` changes to
your Redraft container's `POST /v1/replace` and gets back the edited PDF and a receipt (what was
found, what was changed, whether each change reads back from the edited page). The same file works
for Logic Apps and Power Apps.

| File | What it is |
| --- | --- |
| `apiDefinition.swagger.json` | The action, its inputs and outputs (OpenAPI 2.0, as custom connectors require) |
| `apiProperties.json` | The connection: one API key, sent as `X-API-Key` |

`spikes/test_connector.py` keeps this definition and the server in step: every field the
connector offers is accepted, every field the server returns is declared.

## 1. Run the container with a key

```bash
docker build -t redraft .
docker run -p 7860:7860 -e REDRAFT_API_KEY=choose-a-long-secret redraft
```

Power Automate runs in Microsoft's cloud, so it has to reach the container over HTTPS:

* **To try it**: a temporary tunnel, e.g. `cloudflared tunnel --url http://localhost:7860`, which
  prints an `https://….trycloudflare.com` address.
* **For real use, inside your network**: install the
  [on-premises data gateway](https://learn.microsoft.com/data-integration/gateway/service-gateway-install)
  on a machine that can reach the container, and tick *Connect via on-premises data gateway* on
  the connector's Security page. The container then needs no inbound access from the internet.

The hosted Redraft site is not a target for this connector: it signs users in with accounts and
does not take API keys.

## 2. Import the connector

1. In [make.powerautomate.com](https://make.powerautomate.com): **Data → Custom connectors →
   New custom connector → Import an OpenAPI file**. Name it *Redraft* and pick
   `apiDefinition.swagger.json`.
2. **General**: set **Host** to your container's address without `https://`
   (e.g. `abc-def.trycloudflare.com`), Base URL `/`.
3. **Security**: *API Key*, parameter label `API key`, name `X-API-Key`, location *Header* (the
   import fills these in).
4. **Create connector**, then **Test → New connection**, paste the key from step 1.

With the [Power Platform CLI](https://learn.microsoft.com/power-platform/developer/cli/introduction)
the same import is one command, after setting `host` in the definition:

```bash
pac connector create --api-definition-file apiDefinition.swagger.json --api-properties-file apiProperties.json
```

## 3. A first flow

*When a file is created* (OneDrive or SharePoint, a folder such as `/Redraft/in`) →
**Redraft · Replace text in a PDF**:

* **PDF file content**: *File Content* from the trigger.
* **Changes**: switch the field to array input and enter, for example
  `[{"find": "Essaouira", "replace": "Marrakech"}]`.

→ *Create file* in `/Redraft/out`, **File Content**: the expression
`base64ToBinary(body('Replace_text_in_a_PDF')?['pdf'])`.

**Edited PDF** is empty when nothing could be changed, so a flow can never mistake an untouched
file for a corrected one. Add a *Condition* on **All changes made** (`receipt.ok`) to route
partial results to a person. Set **Fail unless every change is made** if any missed change should
fail the run instead (HTTP 422, with the receipt in the error body).

Limits: 25 MB per PDF, 200 changes per call, 2,000 characters per text.
