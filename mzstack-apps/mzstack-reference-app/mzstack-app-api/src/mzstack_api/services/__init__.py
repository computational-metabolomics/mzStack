"""Everything that talks to `mzstack`.

Blueprints parse requests and choose status codes; services do the work and
return plain Python. Keeping the split means a service can be tested by
calling it, with no request context in sight.
"""
