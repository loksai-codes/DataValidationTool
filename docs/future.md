**Future extensibility requirements**

Build the initial Data Validation Platform so that multiple environments, Okta authentication, and initiative-specific configurations can be added without redesigning the core application.

1. Separate source and target connection configuration from the validation engine.
2. Support named connections through a configuration-driven connector registry.
3. Keep environment configuration separate from initiative configuration.
4. Design authentication as a pluggable component so Okta-based AWS authentication can be integrated later.
5. Never hardcode AWS account IDs, environment-specific settings, or credentials into the validation engine.
6. Keep credentials and temporary AWS tokens out of job manifests, logs, and persisted job results.
7. Ensure validation jobs record which connection and environment they used, without storing secrets.
8. Initially implement and test Athena → PostgreSQL using the existing authentication setup or an appropriate local development configuration.
9. Do not implement a fake Okta integration or claim that multi-environment support is complete before it has been implemented and tested.

Prioritize a fully working core validation engine, durable job resumption, the initial connectors, and the Streamlit interface. Add Okta authentication, DEV/PRE-PROD/PROD environment management, and initiative management in subsequent phases.