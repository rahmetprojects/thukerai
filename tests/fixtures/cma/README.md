# CMA API fixtures (real responses, trimmed)

Captured on 2026-10-28 from https://www.uaecma.gov.ae by replaying the page's own API calls
inside a real browser session. Lists were shortened to a few records and long repetitive fields
were removed; every value that remains is exactly what the CMA returned.
The anti-forgery token in `page_warnings.html` is FAKE (the real one is session-bound and secret).

| file | what it is |
|---|---|
| page_warnings.html | the bits of the warnings page the client needs: hidden token input + list options |
| warnings_p1.json | GetContentList typeId 1071, page 1 (both languages, paired by groupId) |
| warnings_beyond.json | a page number past the end: valid envelope, empty `contents` |
| violations.json | GetContentList typeId 1039 |
| local_funds.json | GetContentList typeId 1036 |
| licensed_p1.json | GeIntegrationResult 2045 page 1 (`total` = pages, `records` = rows) |
| licensed_beyond.json | 2045 past the end: note the bogus `total:1, records:1` |
| activities.json | GeIntegrationResult 2047 (licensed activities) |
| finfluencers.json | GeIntegrationResult 2056 |
| err_no_token.json | HTTP 403 when the token or session cookie is missing |
| err_no_lang.json | HTTP **200** with an error inside, when the languageId header is missing |
| appraisers.html | the static HTML table on the appraisers page |
