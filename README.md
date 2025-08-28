## Setup (local)

Create local config files from the provided examples (do NOT commit these):

cp auth_config.example.yaml auth_config.yaml     # then edit locally: paste hashed passwords and a secret cookie key
cp config.example.json config.json               # then edit locally if you need different start values

Never commit `auth_config.yaml` or `config.json`. They are included in `.gitignore`.
