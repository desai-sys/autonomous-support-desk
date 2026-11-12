"""
App builder: describe an application, review it, approve, and it is deployed.

The model's job is kept as small as possible, because that is what makes it
reliable on mid-sized models:

- ``blueprints``  platform-owned, tested CloudFormation for a few app shapes.
                  The model never writes infrastructure or IAM.
- ``runtime_handler``  the entry point shipped inside every generated app
                  (serves the page, dispatches the API, provides storage). The
                  model only writes the page and a small ``handle``/``run`` function.
- ``checks``      static checks on what the model wrote.
- ``pipeline``    plan → write each file as plain text → check → repair → store
                  → queue the deployment for approval.
"""
