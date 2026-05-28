# Phase 1: Lead Scoring Algorithm - Context

**Gathered:** 2026-03-26
**Status:** Ready for implementation

<domain>
## Phase Boundary
Implement a scoring system to prioritize leads based on their digital presence and service needs. The score will be added as a dedicated column in the Google Sheet.
</domain>

<decisions>
## Implementation Decisions

### Scoring Weights (Total: ~150 possible)
- **D-01: No Website** -> **+50 points** (Primary lead for web dev)
- **D-02: Missing Facebook** -> **+15 points**
- **D-03: Missing Instagram** -> **+15 points**
- **D-04: Missing LinkedIn/Twitter** -> **+10 points each**
- **D-05: No Email Found** -> **+20 points**
- **D-06: Low Rating (< 4.0)** -> **+20 points**
- **D-07: Healthy Business Override** -> **100 points** if business has Website + Email + (any Social) AND Rating > 4.0.

### Logic Integration
- The score will be calculated in `main.py` after both Maps scraping and Website extraction are complete.
- A new column "Lead Score" will be added to the Google Sheet.
</decisions>
