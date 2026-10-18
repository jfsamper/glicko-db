# Glicko BD

Documentação: [Español](README.md) | [English](README.en.md) | Português

Glicko BD é uma aplicação Flask e SQLite para gerenciar jogadores, ratings, partidas e torneios de uma comunidade de Go. Ela oferece classificações e estatísticas públicas, além de telas administrativas protegidas para importações, configuração do rating, cópias de segurança e operações de torneios.

## Índice

- [Recursos](#recursos)
- [Ajuda do usuário](#ajuda-do-usuário)
- [Requisitos](#requisitos)
- [Execução local](#execução-local)
- [Configuração](#configuração)
- [Plano do projeto](#plano-do-projeto)
- [Desenvolvimento](#desenvolvimento)
  - [Organização do código](#organização-do-código)
  - [Instalação em hospedagem Linux](#instalação-em-hospedagem-linux)
- [Licença e atribuição](#licença-e-atribuição)

## Recursos

- Classificações públicas, busca, perfis, histórico de partidas e gráficos de rating
- Cálculo Glicko-2 configurável e interface em espanhol, inglês e português
- Importação de Excel, CSV e OpenGotha; administração de jogadores, partidas e torneios
- Sistemas suíço, suíço por categoria, suíço acelerado e McMahon
- Registro de membros e envio de resultados para aprovação administrativa
- Relatórios filtráveis com exportação CSV/PDF, notícias e biblioteca pública de SGF
- Cópias de segurança, restauração protegida e migrações SQLite
- Classificações por país, região, idade e categoria feminina
- Partidas com handicap, BYE e ausências

## Ajuda do usuário

O guia da interface é a referência para as tarefas diárias. Ele também está disponível pelo botão `?` na barra superior ou em `/help?lang=pt`.
Em navegadores móveis compatíveis, você pode adicionar o Glicko BD à tela inicial como aplicativo.

- [Ajuda da interface](docs/user_interface.pt.md)
- [Rotas e notas de integração](docs/api_endpoints.pt.md)

## Requisitos

- Python 3.10 ou posterior
- `pip`
- Pacotes Python:
  - `Flask>=3.0`
  - `Flask-WTF>=1.2`
  - `Werkzeug>=3.0`
  - `openpyxl>=3.1`
  - `reportlab>=4.0`
  - `Pillow==11.3.0` (necessário pelo ReportLab para geração de PDF)
  - `tzdata>=2024.1` (dados de fuso horário no Windows)

## Execução local

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:APP_SECRET_KEY = "substitua-por-um-valor-aleatorio-longo"
$env:ADMIN_PASSWORD = "escolha-uma-senha-segura"
python app.py
```

macOS ou Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export APP_SECRET_KEY="substitua-por-um-valor-aleatorio-longo"
export ADMIN_PASSWORD="escolha-uma-senha-segura"
python app.py
```

Abra `http://127.0.0.1:5000` no navegador. A aplicação cria o banco SQLite em `data/acg_ratings.db` na primeira inicialização.

Apenas para dados de exemplo locais, defina `LOAD_SAMPLE_DATA=1` antes de iniciar. Não use dados de exemplo em um banco de produção.

## Configuração

Configure as variáveis de ambiente necessárias; os valores padrão estão em `config.py`.

- `APP_SECRET_KEY` e `ADMIN_PASSWORD`: chave de sessão e senha de uso único para criar a conta administradora inicial. Use valores seguros em produção.
- `APP_PUBLIC_ORIGIN`: origem HTTPS pública para links de recuperação de senha.
- `DB_PATH`: caminho opcional do banco SQLite.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS` e `MAIL_FROM`: configuração SMTP para recuperação e alertas.
- `RECAPTCHA_SITE_KEY` e `RECAPTCHA_SECRET_KEY`: chaves reCAPTCHA v3 para o registro de membros.
- `LOAD_SAMPLE_DATA=1`: carrega dados de exemplo locais; não use em produção.
- `AUDIT_RETENTION_DAYS` e `SCHEDULED_BACKUP_KEEP`: retenção de auditoria e cópias agendadas.

O fuso padrão é UTC-5. Em produção, use HTTPS e mantenha os segredos fora do código-fonte.

## Plano do projeto

Consulte o [trabalho pendente](FUTURE_FEATURES.md).

## Desenvolvimento

As páginas de ajuda são renderizadas a partir do Markdown. Para exportar cópias HTML estáticas:

```powershell
python scripts/build_help_html.py
```

Execute os testes a partir da raiz:

```powershell
pytest -q
```

### Organização do código

As rotas ficam em `routes/`, a lógica compartilhada em `services/` e os testes em `tests/`. Os arquivos de exemplo dos testes estão versionados.

### Instalação em hospedagem Linux

Use Python 3.10 ou posterior, crie um ambiente virtual e instale as dependências:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=Pillow -r requirements.txt
```

Se o Pillow não tiver um wheel compatível, verifique se a versão do Python e a plataforma da hospedagem são compatíveis. Para problemas comuns, consulte os logs do servidor, o ambiente Python, as dependências, o ponto de entrada WSGI, as permissões e o roteamento. Há ferramentas opcionais de diagnóstico em `scripts/dev_only/`; não as inclua na implantação normal.

## Licença e atribuição

Glicko BD foi originalmente desenvolvido para a comunidade de Go na Colômbia por Juan Felipe Samper em 2026.

O sistema [Glicko-2](https://www.glicko.net/glicko/glicko2.pdf) foi publicado por Mark Glickman em 2022 no domínio público. A implementação em Python é ©2009 Ryan Kirkman e o BesoGo é ©2015-2018 Ye Wang. Ambos são distribuídos sob a [licença MIT](./static/vendor/besogo/LICENSE).
