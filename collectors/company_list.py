"""
Seed list of target companies.

ats_type must be one of: "greenhouse", "lever", "ashby"
ats_identifier is the board token / company slug / org slug used in that ATS's
public API URL. Verify each one before relying on it — board tokens don't
always match the company's public name:

  Greenhouse: https://boards-api.greenhouse.io/v1/boards/{ats_identifier}/jobs
  Lever:      https://api.lever.co/v0/postings/{ats_identifier}?mode=json
  Ashby:      https://api.ashbyhq.com/posting-api/job-board/{ats_identifier}

The list below was verified by hitting each endpoint directly and confirming
it currently lists design-titled roles (Product Designer, UX, etc.) — not
guessed from memory. ATS board tokens can change or be taken down; re-verify
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
]
