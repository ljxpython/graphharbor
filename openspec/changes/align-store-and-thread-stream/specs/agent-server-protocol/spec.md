## MODIFIED Requirements

### Requirement: Capability honesty
The server SHALL report unavailable Extended/Unavailable capabilities
explicitly and SHALL NOT return fabricated success responses for them. Store
is a supported public capability and SHALL not be reported as unavailable.

#### Scenario: Supported Store capability request
- **WHEN** a client calls a supported Store endpoint
- **THEN** the server returns the pinned official-compatible Store response
  rather than an unavailable-capability response

#### Scenario: Unavailable capability request
- **WHEN** a client calls an Unavailable endpoint
- **THEN** the server returns a documented 404/501-style error and capability
  metadata remains accurate
