# Sono Bestie legal pages and jobs feed

- `index.html`, `privacy.html`: support and privacy policy.
- `crawl.py` + `employers.json`: finds open sonographer-type jobs from employers' own public career sites
  (sitemap + schema.org JobPosting data, or the Workday career site's own search). Honours robots.txt,
  identifies itself, waits between requests, stores only title / date / place / type and a link to the employer.
- `jobs.json`: the result, refreshed every 6 hours by GitHub Actions and served to the app by GitHub Pages.
