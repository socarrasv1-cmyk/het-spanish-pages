HET SCRAPER API + GPT ACTION DEPLOYMENT

WHAT IS READY
- FastAPI wrapper around the HET state scraper
- /health
- /states
- /scrape?url=...
- /scrape-all
- Bearer API-key protection
- Render Blueprint file
- GPT Action OpenAPI schema

DEPLOY TO RENDER
1. Put these files in a GitHub repository.
2. In Render choose New > Blueprint and connect that repository.
3. Render reads render.yaml and creates the web service.
4. Copy the deployed HTTPS domain.
5. In openapi-gpt-action.yaml replace:
   https://REPLACE-WITH-YOUR-DEPLOYED-DOMAIN
   with the Render HTTPS domain.
6. In Render copy the generated HET_SCRAPER_API_KEY environment value.

CONNECT TO YOUR GPT
1. Edit the GPT.
2. Open Actions > Create new action.
3. Authentication: API Key.
4. Auth type: Bearer.
5. Paste the HET_SCRAPER_API_KEY value.
6. Paste openapi-gpt-action.yaml into Schema.
7. Test listStatePages, then scrapeStatePage.
8. Test scrapeAllStatePages only after the single-page test passes.

IMPORTANT
- source_url and source_slug are protected and never translated.
- The API returns English content in *_en and blank adjacent *_es fields.
- It extracts Hero H1 through the final FAQ and excludes post-FAQ H2 modules.
- If the FAQ boundary is not found, the API fails that page rather than guessing.
- For a public GPT, provide a valid Privacy Policy URL in the GPT configuration.
- Do not expose the API key in Knowledge files, prompts, source code, or public repositories.
