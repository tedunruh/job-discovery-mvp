"""
Seed list of target companies.

ats_type must be one of: "greenhouse", "lever", "ashby"
ats_identifier is the board token / company slug / org slug used in that ATS's
public API URL. Verify each one before relying on it — board tokens don't
always match the company's public name:

  Greenhouse: https://boards-api.greenhouse.io/v1/boards/{ats_identifier}/jobs
  Lever:      https://api.lever.co/v0/postings/{ats_identifier}?mode=json
  Ashby:      https://api.ashbyhq.com/posting-api/job-board/{ats_identifier}

Open each URL in a browser for a candidate company — if it returns a JSON job
list, you've got the right identifier. Add 20-30 companies you'd actually
apply to below.
"""

COMPANIES = [
    # {"name": "Example Co", "ats_type": "greenhouse", "ats_identifier": "examplecoboardtoken"},
]
