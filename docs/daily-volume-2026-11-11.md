# Publishing restoration — November 11, 2026

The publisher asked why the RSS workflow was not producing several articles a day and
set a target of ten or more, with no daily ceiling: everything that meets the criteria
should publish.

## What was stopping publication

1. **The workflow was switched off.** "RSS to WordPress Automation" had been disabled in
   GitHub since its last scheduled run on October 15, so nothing ran at all.
2. **It was hard-wired to preview.** `PUBLISHER_LIVE: 'false'` made every run read-only
   even if it had been enabled.
3. **The checks rejected about 95% of what the feeds carry.** The 155 feeds are mostly
   agency, school and athletics posts. In a typical 24 hours they carry about 160 items:
   100 under 45 words, 36 of 45–89 words, 15 of 90–179 words and 9 of 180 or more. The old
   rules allowed a standalone article only from 180+ source words (plus a narrow keyword
   list), so the last live runs logged "Fewer than 180 source words" for most items and
   published about one post per run at best.
4. **The full-article floor could not be met honestly.** A 180-word source had to yield a
   300-word body, seven distinct facts and all five Ws with no padding allowed.
5. **One failed request could stop the publisher for good.** Any error after a write
   began (including the public-page check added on October 27, which had never run live)
   paused every later run until someone edited the cached database by hand.
6. **Two posts per run** and a two-hour schedule capped output even on a good day.

## What changed

- The workflow is live and runs hourly: up to five new posts and sixteen model attempts
  per run. There is no daily ceiling.
- Sources of 60–179 words are judged on their own as news briefs (40–250 body words,
  three facts) instead of waiting for three compatible items to form a briefing.
- Full articles need 100+ body words, three paragraphs and five facts.
- A fact whose quotation is not found in the source is dropped instead of discarding
  the whole item; the remaining facts must still pass every check.
- The verifier lists only material problems. Style notes no longer fail an accurate draft.
- Telephone numbers are compared by digits, so `(662)-895-4111` matches `662-895-4111`.
- Each side of a hyphen-joined score or round list is a supported number: a source line
  of `73-75-76` supports "73, 75 and 76". Reversed scores and changed dates still fail.
- A day range shares its month: "Nov. 13-16" matches a source that gives Nov. 13 and
  Nov. 16 separately. A changed end date or month still fails.
- Configured sources are Mississippi organizations, so their own notices are no longer
  rejected for not naming the state.
- Who and what are required. Where, when and why are reported when the source supplies
  them; a source that omits one no longer disqualifies the story.
- An interrupted write is settled from WordPress's own receipt and source-link records
  at the start of the next run. A source whose write reached WordPress is recorded as
  published; one that did not is held, never recreated. A refusal WordPress states
  outright (HTTP 400/409/413/415/422) holds that one item and the run continues.
- A public-page mismatch after WordPress has returned a receipt is reported as a warning
  on a live post, not treated as an unwritten one.
- More than ten similar earlier headlines no longer fails the item; the ten closest are
  compared.
- The two fixed reconciliation post IDs are no longer required to exist.

## What did not change

- Every fact still needs an exact quotation from the source, numbers and direct quotes
  are still checked against it, and a separate verification call must still pass.
- Private sensitive information (sexual offences, suicide, medical or patient records,
  personal identifiers) is still held from every source. See the next section for arrest
  and crime reports, which the publisher approved later the same day.
- Greetings, thank-you posts, teasers and items with no concrete development still fail.
- Image rules are unchanged: 600×400 minimum, required for everything except
  public-service briefs and briefings. The WordPress companion plugin enforces the same.
- Duplicate-source, duplicate-headline and same-event checks are unchanged.

## Arrest and crime reports

The October 27 rule held every allegation and criminal case for separate editorial
approval. Most configured sources are police and sheriff pages, so that was the largest
group of unpublished items. On November 11 the publisher approved publishing them
automatically, within these limits:

- **Official sources only.** The report must come from the agency itself: a police or
  sheriff's department, district attorney, attorney general, Department of Public Safety,
  Department of Corrections, State Auditor, Crime Stoppers, or a city, town or county
  government feed. The list is the `OFFICIAL_AGENCY` pattern in `editorial_safety.py`.
  The same content from a school, athletics department, chamber, venue, podcast or
  reporter is still held before any model call.
- **Attribution and no implied guilt.** The writer must attribute every allegation to the
  agency and say a person was arrested on, charged with or accused of an offence. The
  verifier rejects wording that states or implies guilt or strengthens an allegation.
- **Presumption note.** Every published crime report ends with a fixed line: "An arrest
  or charge is an accusation. Anyone accused is presumed innocent unless proven guilty
  in court."
- **Still held from every source:** sexual offences, suicide, medical or patient records,
  personal identifiers, and anything the extractor flags as private sensitive
  information, including the name of a juvenile or of a sexual-offence victim.
- Crime reports publish only as standalone articles, never inside a multi-source briefing.

To reverse this, make `official_crime_source` in `editorial_safety.py` return `False`.

## Turning it off or down

Set `PUBLISHER_LIVE: 'false'` in `.github/workflows/rss-automation.yml` to return to
preview-only, or disable the workflow in the Actions tab. `MAX_NEW_POSTS_PER_RUN` and
`MAX_POSTS_PER_RUN` can be set as repository variables without a code change.
