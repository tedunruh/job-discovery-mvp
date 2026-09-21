"""
Seed list of target companies.

ats_type must be one of: "greenhouse", "lever", "ashby", "workday", "workable"
ats_identifier's format depends on ats_type. Verify each one before relying
on it — board tokens don't always match the company's public name:

  Greenhouse: board token, e.g. "figma"
              https://boards-api.greenhouse.io/v1/boards/{ats_identifier}/jobs
  Lever:      company slug, e.g. "palantir"
              https://api.lever.co/v0/postings/{ats_identifier}?mode=json
  Ashby:      org slug, e.g. "notion"
              https://api.ashbyhq.com/posting-api/job-board/{ats_identifier}
  Workday:    "{tenant}.{instance}/{site}", e.g. "workhuman.wd1/WorkhumanCareers"
              Unlike the other three, there's no guessable URL pattern - tenant,
              instance (wd1/wd3/wd5/...), and site vary per company and can
              change over time (tenants get migrated between instances). Find
              them by opening the company's Workday careers page, opening
              browser devtools' Network tab, and reading them off a request to
              .../wday/cxs/<tenant>/<site>/jobs. Also note: Workday's public API
              only exposes a relative posted date ("Posted 3 Days Ago"), so
              ats_posted_at for Workday postings is day-precision at best -
              less accurate than the other three collectors.
  Workable:   account slug, e.g. "airhelp"
              https://apply.workable.com/api/v1/widget/accounts/{ats_identifier}
              Gives a real published_on date (day precision, no time) - more
              reliable than Workday's relative text, though still less precise
              than Greenhouse/Lever/Ashby's full timestamps. Watch for accounts
              that are recruiting agencies/talent marketplaces rather than a
              single company (e.g. one account with 2000+ jobs across many
              employers) - skip those, they're not what this list is for.

The list below was verified by hitting each endpoint directly and confirming
it currently lists design-titled roles (Product Designer, UX, etc.) — not
guessed from memory. ATS identifiers can change or be taken down; re-verify
if a collector starts failing for one of these. Edit freely to match who you
actually want to apply to.
"""

COMPANIES = [
    # Greenhouse
    {"name": "Figma", "ats_type": "greenhouse", "ats_identifier": "figma"},
    {"name": "Airbnb", "ats_type": "greenhouse", "ats_identifier": "airbnb"},
    {"name": "Stripe", "ats_type": "greenhouse", "ats_identifier": "stripe"},
    {"name": "Duolingo", "ats_type": "greenhouse", "ats_identifier": "duolingo"},
    {"name": "Robinhood", "ats_type": "greenhouse", "ats_identifier": "robinhood"},
    {"name": "Pinterest", "ats_type": "greenhouse", "ats_identifier": "pinterest"},
    {"name": "Coinbase", "ats_type": "greenhouse", "ats_identifier": "coinbase"},
    {"name": "Databricks", "ats_type": "greenhouse", "ats_identifier": "databricks"},
    {"name": "Affirm", "ats_type": "greenhouse", "ats_identifier": "affirm"},
    {"name": "Lyft", "ats_type": "greenhouse", "ats_identifier": "lyft"},
    {"name": "Instacart", "ats_type": "greenhouse", "ats_identifier": "instacart"},
    {"name": "Block", "ats_type": "greenhouse", "ats_identifier": "block"},
    {"name": "Gusto", "ats_type": "greenhouse", "ats_identifier": "gusto"},
    {"name": "Discord", "ats_type": "greenhouse", "ats_identifier": "discord"},
    {"name": "Intercom", "ats_type": "greenhouse", "ats_identifier": "intercom"},
    {"name": "Asana", "ats_type": "greenhouse", "ats_identifier": "asana"},
    {"name": "Brex", "ats_type": "greenhouse", "ats_identifier": "brex"},
    {"name": "Anthropic", "ats_type": "greenhouse", "ats_identifier": "anthropic"},
    {"name": "Vercel", "ats_type": "greenhouse", "ats_identifier": "vercel"},
    {"name": "Scale AI", "ats_type": "greenhouse", "ats_identifier": "scaleai"},
    {"name": "Datadog", "ats_type": "greenhouse", "ats_identifier": "datadog"},
    {"name": "Samsara", "ats_type": "greenhouse", "ats_identifier": "samsara"},
    {"name": "New Relic", "ats_type": "greenhouse", "ats_identifier": "newrelic"},
    {"name": "Fivetran", "ats_type": "greenhouse", "ats_identifier": "fivetran"},
    {"name": "Justworks", "ats_type": "greenhouse", "ats_identifier": "justworks"},
    {"name": "Cloudflare", "ats_type": "greenhouse", "ats_identifier": "cloudflare"},
    {"name": "Mixpanel", "ats_type": "greenhouse", "ats_identifier": "mixpanel"},
    {"name": "Amplitude", "ats_type": "greenhouse", "ats_identifier": "amplitude"},
    {"name": "Airtable", "ats_type": "greenhouse", "ats_identifier": "airtable"},
    {"name": "Squarespace", "ats_type": "greenhouse", "ats_identifier": "squarespace"},
    # Lever
    {"name": "Palantir", "ats_type": "lever", "ats_identifier": "palantir"},
    {"name": "Wealthfront", "ats_type": "lever", "ats_identifier": "wealthfront"},
    {"name": "Houzz", "ats_type": "lever", "ats_identifier": "houzz"},
    {"name": "Outreach", "ats_type": "lever", "ats_identifier": "outreach"},
    # Ashby
    {"name": "Notion", "ats_type": "ashby", "ats_identifier": "notion"},
    {"name": "Ramp", "ats_type": "ashby", "ats_identifier": "ramp"},
    {"name": "Linear", "ats_type": "ashby", "ats_identifier": "linear"},
    {"name": "Vanta", "ats_type": "ashby", "ats_identifier": "vanta"},
    {"name": "Replit", "ats_type": "ashby", "ats_identifier": "replit"},
    {"name": "Watershed", "ats_type": "ashby", "ats_identifier": "watershed"},
    {"name": "Supabase", "ats_type": "ashby", "ats_identifier": "supabase"},
    {"name": "Synthesia", "ats_type": "ashby", "ats_identifier": "synthesia"},
    {"name": "ElevenLabs", "ats_type": "ashby", "ats_identifier": "elevenlabs"},
    {"name": "Baseten", "ats_type": "ashby", "ats_identifier": "baseten"},
    {"name": "WorkOS", "ats_type": "ashby", "ats_identifier": "workos"},
    {"name": "Render", "ats_type": "ashby", "ats_identifier": "render"},
    {"name": "Resend", "ats_type": "ashby", "ats_identifier": "resend"},
    {"name": "Clerk", "ats_type": "ashby", "ats_identifier": "clerk"},
    {"name": "Secureframe", "ats_type": "ashby", "ats_identifier": "secureframe"},
    {"name": "Deepgram", "ats_type": "ashby", "ats_identifier": "deepgram"},
    {"name": "Modal", "ats_type": "ashby", "ats_identifier": "modal"},
    # Workday
    {"name": "Workhuman", "ats_type": "workday", "ats_identifier": "workhuman.wd1/WorkhumanCareers"},
    {"name": "Workiva", "ats_type": "workday", "ats_identifier": "workiva.wd503/careers"},
    {"name": "Zendesk", "ats_type": "workday", "ats_identifier": "zendesk.wd1/zendesk"},
    {"name": "ServiceTitan", "ats_type": "workday", "ats_identifier": "servicetitan.wd1/ServiceTitan"},
    # Workable
    {"name": "AirHelp", "ats_type": "workable", "ats_identifier": "airhelp"},
    {"name": "Hospitable", "ats_type": "workable", "ats_identifier": "hospitable"},
    {"name": "Swimply", "ats_type": "workable", "ats_identifier": "swimply"},
    {"name": "Innovaccer", "ats_type": "workable", "ats_identifier": "innovaccer-analytics"},
]
