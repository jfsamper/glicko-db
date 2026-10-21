# Glicko BD

Documentación: Español | [English](README.en.md) | [Português](README.pt.md)

Glicko BD es una aplicación Flask y SQLite para administrar jugadores, ratings, partidas y torneos de una comunidad de Go. Ofrece posiciones y estadísticas públicas, junto con pantallas de administración protegidas para importaciones, configuración del rating, copias de seguridad y operaciones de torneos.

## Tabla de contenidos

- [Funciones](#funciones)
- [Ayuda para usuarios](#ayuda-para-usuarios)
- [Requisitos](#requisitos)
- [Ejecución local](#ejecución-local)
- [Configuración](#configuración)
- [Hoja de ruta del proyecto](#hoja-de-ruta-del-proyecto)
- [Desarrollo](#desarrollo)
  - [Organización del código](#organización-del-código)
  - [Instalación en hosting Linux](#instalación-en-hosting-linux)
  - [Copias programadas y pruebas de restauración](#copias-programadas-y-pruebas-de-restauración)
- [Licencia y atribución](#licencia-y-atribución)

## Funciones

- Clasificaciones, búsqueda, perfiles, historial de partidas y gráficos de rating
- Cálculo Glicko-2 configurable e interfaz en español, inglés y portugués
- Importación de Excel, CSV y OpenGotha; administración de jugadores, partidas y torneos
- Sistemas Suizo, Suizo por Categoría, Suizo Acelerado y McMahon
- Registro de miembros y envío de resultados para aprobación administrativa
- Reportes con filtros y exportación CSV/PDF, además de noticias y biblioteca pública de registros SGF
- Copias de seguridad, restauración protegida y migraciones SQLite
- Clasificaciones por país, región, edad y categoría femenina
- Torneos con hándicap, descansos y ausencias
- Las partidas de torneos en borrador o cancelados se excluyen de los perfiles públicos y los ratings

## Ayuda para usuarios

La guía de la interfaz es la referencia para las tareas diarias. También está disponible desde el botón `?` de la barra superior o en `/help?lang=es`.
En navegadores móviles compatibles, puedes agregar Glicko BD a la pantalla de inicio como aplicación.

- [Ayuda de la interfaz](docs/user_interface.md)
- [Rutas y notas de integración](docs/api_endpoints.md)

## Requisitos

- Python 3.10 o posterior
- `pip`
- Paquetes de Python:
  - `Flask>=3.0`
  - `Flask-WTF>=1.2`
  - `Werkzeug>=3.0`
  - `openpyxl>=3.1`
  - `reportlab>=4.0`
  - `Pillow==11.3.0` (requerido por ReportLab para generar PDF)
  - `tzdata>=2024.1` (Windows time zone data)

## Ejecución local

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:APP_SECRET_KEY = "reemplaza-por-un-valor-aleatorio-largo"
$env:ADMIN_PASSWORD = "elige-una-contrasena-segura"
python app.py
```

macOS o Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export APP_SECRET_KEY="reemplaza-por-un-valor-aleatorio-largo"
export ADMIN_PASSWORD="elige-una-contrasena-segura"
python app.py
```

Abre `http://127.0.0.1:5000` en el navegador. La aplicación crea la base de datos SQLite en `data/acg_ratings.db` durante el primer inicio.

Solo para datos de ejemplo locales, define `LOAD_SAMPLE_DATA=1` antes de iniciar. No uses datos de ejemplo en una base de datos de producción.

## Configuración

Configura los valores necesarios en el entorno; los valores predeterminados están en `config.py`.

- `APP_SECRET_KEY` y `ADMIN_PASSWORD`: clave de sesión y contraseña de un solo uso para crear la cuenta administradora inicial. Usa secretos seguros en producción.
- `APP_PUBLIC_ORIGIN`: origen HTTPS público para enlaces de recuperación de contraseña.
- `DB_PATH`: ubicación opcional de la base de datos SQLite.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS` y `MAIL_FROM`: configuración SMTP para recuperación y alertas.
- `RECAPTCHA_SITE_KEY` y `RECAPTCHA_SECRET_KEY`: claves reCAPTCHA v3 para el registro de miembros.
- `LOAD_SAMPLE_DATA=1`: carga datos de ejemplo locales; no lo uses en producción.
- `AUDIT_RETENTION_DAYS` y `SCHEDULED_BACKUP_KEEP`: retención de auditoría y copias programadas.

La aplicación usa UTC-5 de forma predeterminada. En producción, usa HTTPS y no guardes secretos en el código.

## Hoja de ruta del proyecto

Consulta la [hoja de ruta](FUTURE_FEATURES.md), que reúne las propuestas de funciones y operaciones y el [plan detallado de migración a una API autenticada](FUTURE_FEATURES.md#authenticated-api-migration). La API está planificada, no implementada; las páginas web públicas y exportaciones actuales siguen siendo las interfaces disponibles.

## Desarrollo

Las guías se renderizan desde Markdown. Para exportar copias HTML estáticas:

```powershell
python scripts/build_help_html.py
```

Ejecuta las pruebas desde la raíz:

```powershell
pytest -q
```

Las pruebas opcionales de navegación en Chromium requieren Playwright:

```powershell
python -m pip install playwright
python -m playwright install chromium
python -m pytest tests\test_navigation_browser.py -q
```

### Organización del código

Las rutas de la aplicación están en `routes/`, la lógica compartida en `services/` y las pruebas en `tests/`. Los archivos de ejemplo necesarios para las pruebas están versionados.

### Instalación en hosting Linux

Usa Python 3.10 o posterior, crea un entorno virtual e instala las dependencias:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=Pillow -r requirements.txt
```

Si Pillow no tiene wheels compatibles, revisa que la versión de Python y la plataforma del hosting sean compatibles. Para diagnosticar fallos comunes, revisa los registros del servidor, el entorno Python, las dependencias, el punto de entrada WSGI, los permisos y el enrutamiento. Hay herramientas de diagnóstico opcionales en `scripts/dev_only/`; no las incluyas en el despliegue normal.

### Copias programadas y pruebas de restauración

Programa `scripts/scheduled_backup.py` con el mismo Python, directorio de trabajo y variables de entorno (`DB_PATH` y `SCHEDULED_BACKUP_KEEP`) que usa la aplicación. El trabajo verifica la copia y una restauración en una base temporal antes de aplicar la retención; termina con código 1 si falla. En Windows Task Scheduler, configura:

- **Programa**: `C:\ruta\glicko-db\.venv\Scripts\python.exe`
- **Argumentos**: `scripts\scheduled_backup.py`
- **Iniciar en**: `C:\ruta\glicko-db`

En Linux, una entrada de cron diaria puede ser:

```cron
0 2 * * * cd /srv/glicko-db && .venv/bin/python scripts/scheduled_backup.py >> /var/log/glicko-db-backup.log 2>&1
```

Configura en el entorno del programador las mismas variables de producción; cron y Task Scheduler no necesariamente heredan el entorno del servidor web. Revisa `/admin/backups` como administrador para comprobar la frescura de ratings, la última copia y prueba de restauración, y los fallos operativos. Practica la restauración en una instancia desechable. Para sustituir la base activa, programa una ventana de mantenimiento, bloquea nuevas escrituras y espera a que terminen las solicitudes en curso durante el cambio final. Las copias SQLite no contienen `uploads/sgf`; protege y restaura esa carpeta por separado.

## Licencia y atribución
Glicko-db fue originalmente desarrollado para la comunidad de Go en Colombia por Juan Felipe Samper en 2026. 

El sistema [Glicko-2](https://www.glicko.net/glicko/glicko2.pdf) fue publicado por Mark E. Glickman en 2022 al dominio público. La implementación en Python es ©2009 Ryan Kirkman y BesoGo es ©2015-2018 Ye Wang. Ambas se distribuyen bajo la [licencia MIT](./static/vendor/besogo/LICENSE).
