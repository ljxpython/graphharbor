## ADDED Requirements

### Requirement: Durable Store resources
The system SHALL persist supported Store resources through the existing
PostgreSQL-backed Store and preserve them across API/worker restart.

#### Scenario: Store survives restart
- **WHEN** an item is written, GraphHarbor services restart, and the same
  authenticated principal reads the item
- **THEN** the item is returned with the official-compatible representation
