"""
Seed list of target companies.

ats_type must be one of: "greenhouse", "lever", "ashby", "workday"
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
    # Lever
    {"name": "Palantir", "ats_type": "lever", "ats_identifier": "palantir"},
    {"name": "Wealthfront", "ats_type": "lever", "ats_identifier": "wealthfront"},
    {"name": "Houzz", "ats_type": "lever", "ats_identifier": "houzz"},
    # Ashby
    {"name": "Notion", "ats_type": "ashby", "ats_identifier": "notion"},
    {"name": "Ramp", "ats_type": "ashby", "ats_identifier": "ramp"},
    {"name": "Linear", "ats_type": "ashby", "ats_identifier": "linear"},
    {"name": "Vanta", "ats_type": "ashby", "ats_identifier": "vanta"},
    {"name": "Replit", "ats_type": "ashby", "ats_identifier": "replit"},
    {"name": "Watershed", "ats_type": "ashby", "ats_identifier": "watershed"},
    {"name": "Supabase", "ats_type": "ashby", "ats_identifier": "supabase"},
    # Workday
    {"name": "Workhuman", "ats_type": "workday", "ats_identifier": "workhuman.wd1/WorkhumanCareers"},
    {"name": "Workiva", "ats_type": "workday", "ats_identifier": "workiva.wd503/careers"},
]
