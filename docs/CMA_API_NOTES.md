# CMA open-data API: what was verified on the live site

Verified on **2026-10-28** by loading `https://www.uaecma.gov.ae/en/open-data/...` in a real browser and
replaying the pages' own API calls from inside the page (same-origin). Trimmed real responses are kept in
`tests/fixtures/cma/`. Re-check these notes if a dataset starts failing.

## Session and headers

* The token is server-rendered in the page HTML:
  `<input type="hidden" name="__RequestVerificationToken" value="anonymous:<session>:<id>:<unix time>:<hmac>">`
* The site's jQuery setup (`main.tailwind.min.js`) adds to every AJAX call:
  `RequestVerificationToken: <that value>`, `languageId: <1|2>`, `languageCode: <en-GB|ar-AE>`.
* Tests from the page:

| request | answer |
|---|---|
| token + cookie + language headers | HTTP 200 `{"code":200,"data":{...}}` |
| no token | HTTP 403 `{"error":"token_invalid","message":"Invalid anti-forgery token","code":403}` |
| token but no cookie (`credentials: omit`) | HTTP 403 `token_invalid`: the token is bound to the session cookie |
| no language headers | **HTTP 200** `{"code":500,"error":{"ClassName":"System.Exception",...}}` |

## GetContentList  (`POST /api/PublicApi/GetContentList`)

Body (from the page's `cotentLitingOptions_*.postData`):
```json
{"htmlTemplatePath":"ajax-templates/pages/warnings.html","loadResultAsHTML":false,"typeId":1071,
 "languageId":1,"languageCode":"en-GB","pageIndex":1,"pageSize":100,"isArchived":false,
 "FilterGroups":[{"OR_AND_Operator":"AND","Filters":[{"Key":"Status","Value":"Published",...},
                 {"Key":"Status","Value":"Active",...},{"Key":"Status","Value":"NotArchived",...}]}],
 "Sort":[{"Key":"ItemDate1","Operator":"DESC"}]}
```
Answer: `{"code":200,"data":{"page":1,"records":455,"totalPages":5,"contents":[...]},"dateModified":"..."}`

| page | typeId | template | sort | rows seen |
|---|---|---|---|---|
| Warnings | 1071 | warnings.html | ItemDate1 DESC | 455 items active (476 incl. archived), EN + AR |
| Violations and Violators | 1039 | violations.html | attr_order ASC | 10 |
| Financial auditors | 1045 | financial-auditors.html | attr_ordering ASC | 14 |
| Organizers of general assemblies | 1070 | organizer-of-general-assemblies.html | attr_order ASC | 7 |
| Local funds | 1036 | localfund.html | attr_order ASC | 83 |
| Passported funds | 1037 | passportingfund.html | ItemDate1 DESC | 144 |
| End-of-service gratuity funds | 1076 | end-of-service.html | ItemDate1 DESC | 13 |
| Numbers and statistics | 16 | opendata.html | - | 52 |

Paging: `pageIndex` starts at 1 (0 is treated as 1); a page past the end returns `contents: []`, `records: 0`;
`pageSize` up to 500 worked (we use 100). Warnings return BOTH languages as separate items sharing a `groupId`.
Item fields: `id, groupId, title, languageId, isoItemDate1, shortItemDate1, lastUpdateDate, attr{...}`.

## GeIntegrationResult  (`POST /api/PublicApi/GeIntegrationResult`, the site's own spelling)

Body: `{"integrationId": <id>, "urlParameters": {...}}`.
Answer: `{"code":200,"data":{"total":<pages>,"records":<rows>,"page":p,"items":[...]},"headers":{...},"dateModified":"..."}`

| integration | use | parameters | item fields |
|---|---|---|---|
| 2045 | licensed companies | pageIndex, pageSize, type (activity id or ""), keyword, languageId, languageCode | code, name, status, website, year |
| 2047 | licensed activities | languageCode | companyId (= activity id), title |
| 2056 | authorised finfluencers | pageIndex 1, pageSize 2147483647 | name, nameAR, regNo, issueDate, validityDate, phone, email, socialMedia, year |
| 2055 | one company's details | companyId | (not used) |

Quirk: `total`/`records` are true only on page 1 when there is more than one page (e.g. 4 / 333 at pageSize 100).
Later pages, and any single-page answer, report `total: 1, records: 1`.

## Not machine-readable / not fetched

* Appraisers of in-kind contributions: a static `<table>` (Company Name, Date of Registration) - fetched as HTML.
* Virtual Asset Service Providers: a link to VARA's public register only.
* Graphs and statistics: interactive charts only.
