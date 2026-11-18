# Optional DeepSeek evidence selection

Keyword retrieval and source inspection work locally without AI. To make DeepSeek
available, supply your own key to the app process via the `DEEPSEEK_API_KEY`
environment variable using private environment/secret settings. Do not enter a key
into the app, a chat, a repository, or an example file. A configured process must
still obtain your approval before sending an excerpt-selection request. Omit the key
to keep the app fully local.

If approved, requests go directly to
[DeepSeek Chat Completions](https://api-docs.deepseek.com/) over verified HTTPS using
the configured `DEEPSEEK_MODEL` (default `deepseek-flash`, live availability still to be verified). The evidence-selection request contains the question (at most 600 characters) and
at most four locally retrieved snippets (first 1,000 characters each), labeled
using per-request IDs. It never uploads PDF files or transmits filenames, page
references, or the local index. For very short documents, snippets may contain
much or all of the extracted text; don't use the online option for materials you
aren't permitted to share. DeepSeek may apply its own processing/retention
policies and API charges. Requests have no tool access, no redirects, no proxy
inheritance, no automatic retries, a socket timeout and bounded input/output.

The [structured output](https://api-docs.deepseek.com/guides/json_mode/) contains
only quote suggestions and opaque IDs. The server accepts at most three exact
contiguous excerpts from the actual sent snippets and adds source labels from its
local records; model-generated explanations and citations are never displayed.
An exact quote may still be irrelevant or missing important surrounding context,
so check the referenced passage before relying on it. Unsupported or malformed
suggestions result in abstention. No live API call was used in testing: run
`python -m unittest -v test_provider` for the injected-transport tests.

The competition workbench also provides a separate, per-request opt-in review at `/synthesis`. Select PDFs to preview their original abstract, citation and future-work passages locally. The online option sends these extracts with opaque labels and checks reference IDs on the returned review paragraphs. Review prose is model-authored and requires a reader to check whether each cited extract supports the claim. Offline contract tests exercise the model response path.
