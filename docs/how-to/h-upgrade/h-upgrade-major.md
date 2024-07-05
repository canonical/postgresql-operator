# Perform a major upgrade

**Example**: PostgreSQL 14 -> PostgreSQL 15

[note type="negative"]
Currently, this charm only supports PostgreSQL 14. Therefore, only [minor upgrades](/t/12089) are possible. 

Canonical is **NOT** planning to support in-place upgrades for the major version change. The new PostgreSQL cluster will have to be installed nearby, and the data will be copied from the old to the new installation. After announcing the next PostgreSQL major version support, the appropriate manual will be published here.
[/note]