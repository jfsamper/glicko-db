# Glicko BD

Documentación: Español | [English](README.en.md) | [Português](README.pt.md)

Glicko BD es una aplicación Flask y SQLite para administrar jugadores, ratings, partidas y torneos de una communidad de Go. Ofrece posiciones y estadísticas públicas, junto con pantallas de administración protegidas para importaciones, configuración del rating, copias de seguridad y operaciones de torneos.

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
- [Licencia y atribución](#licencia-y-atribución)

## Funciones

- Posiciones públicas, búsqueda de jugadores, perfiles, historial de partidas, gráficos de rating y conversión de categorías
- Clasificaciones por país, región de América Latina y el Caribe, sub-18 y categoría femenina, con filtros de grupo por casillas; perfiles con país, año de nacimiento y una marca para mujeres
- Cálculo Glicko-2 con parámetros de rating y categoría configurables
- Interfaz pública en español, inglés y portugués
- Administración de jugadores y partidas con paginación, filtros y ordenación consistente
- Biblioteca pública de registros SGF; las cuentas autenticadas pueden cargar archivos sin vincularlos, y los directores de torneo, operadores y administradores pueden vincularlos o desvincularlos
- Importación de libros Excel (XLSX), OpenGotha (XML) y archivos de partidas (CSV)
- Creación y edición de torneos, importación de OpenGotha, emparejamientos, registro de resultados, clasificación y exportación
- Registro de cuentas de miembros y envío de resultados individuales para aprobación administrativa
- Reportes públicos por periodo (por defecto, todo el tiempo) con filtros por jugador, exportación CSV/PDF localizada, cambios de rating y rendimiento por oponente, país y club
- Noticias publicadas por administradores, con enlaces rápidos a jugadores, torneos, partidas y registros SGF
- Sistemas Suizo, Suizo por Categoría, Suizo Acelerado y McMahon
- Manejo de descansos y ausencias, copias de seguridad, protecciones de restauración y migraciones SQLite
- Alertas por correo a administradores activos ante fallos críticos de base de datos, reproducción de ratings o creación/restauración de copias, y ante límites de inicio de sesión alcanzados repetidamente en 30 días
- Solo los torneos activos y completados aparecen en la lista pública; directores, operadores y administradores pueden mostrar los borradores. Los perfiles solo muestran torneos completados y solo las partidas de torneos completados afectan los ratings.
- Partidas con hándicap en piedras (estilo Go), con sugerencia automática por diferencia de categoría y ajuste de rating estilo OGS
- Instalación como aplicación desde navegadores móviles compatibles, excepto Safari

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

Los valores predeterminados están en `config.py`.

- `APP_SECRET_KEY`: clave de firma de las sesiones Flask. Debe configurarse en producción.
- `ADMIN_PASSWORD`: contraseña usada una sola vez para crear la cuenta administrativa inicial; no reemplaza el inicio de sesión con cuentas personales.
- `APP_PUBLIC_ORIGIN`: origen HTTPS público, por ejemplo `https://colombiago.org`, para construir enlaces de recuperación sin confiar en el encabezado Host. No incluyas una ruta ni parámetros. Si no se configura, no se envían correos ni se crean tokens de recuperación.
- `LOAD_SAMPLE_DATA=1`: importa `rank-final.xlsx` si existe y reemplaza el conjunto de datos actual; solo para desarrollo local.
- `DB_PATH`: ubicación de la base de datos SQLite, definida en `config.py`.
- De forma predeterminada, los eventos operativos de la aplicación se emiten como líneas JSON en stderr; los mensajes del servidor de desarrollo y las solicitudes HTTP de Werkzeug se muestran como texto normal para conservar su formato y colores de terminal. Los formatos configurados por el servidor anfitrión se conservan.
- `AUDIT_RETENTION_DAYS`: número de días para conservar eventos de auditoría; el valor predeterminado es `730`.
- `SCHEDULED_BACKUP_KEEP`: número de copias programadas que se conservan; el valor predeterminado es `14`, con mínimo `1`.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS` y `MAIL_FROM`: configuración SMTP para recuperación de contraseñas y alertas operativas enviadas a administradores activos con correo; `PASSWORD_RESET_TTL_SECONDS` controla la caducidad del enlace y usa 3600 segundos por defecto. Las alertas de acceso se envían cuando el mismo origen IP o nombre de cuenta alcanza el límite más de una vez en 30 días; no se aplica enfriamiento de alertas.
- `RECAPTCHA_SITE_KEY` y `RECAPTCHA_SECRET_KEY`: claves de Google reCAPTCHA v3 para el formulario de registro de miembros. La clave secreta se verifica en el servidor y nunca debe exponerse al navegador.
- `RECAPTCHA_MIN_SCORE`: puntuación v3 mínima aceptada para el registro; por defecto es `0.5`.
- `RECAPTCHA_EXPECTED_HOSTNAME`: comprobación opcional del hostname en la respuesta; déjalo vacío si la clave sirve para varios hostnames configurados.

- La hora predeterminada es UTC-5. Cada cuenta puede elegir una zona IANA; las partidas del mismo día se procesan por ronda y luego por orden de inserción.
- `/reports` usa rangos inclusivos `start_date` y `end_date` en la zona fija del servidor. Los totales de pantalla y de las exportaciones CSV/PDF se calculan con los mismos filtros. El cambio de rating compara la última instantánea anterior al inicio con la última hasta el final; las partidas del primer día están incluidas.
- Las cuentas tienen roles `administrator`, `tournament_director`, `operator` y `member`. Una cuenta puede tener varios roles y obtiene los permisos de todos ellos. Los miembros solo envían resultados de su jugador vinculado; los demás roles revisan la cola de aprobación.
- El inicio de sesión limita los intentos por IP y nombre de usuario antes de comprobar la contraseña; los límites se configuran en `/admin/settings`.
- Las sugerencias para jugadores pendientes solo vinculan una cuenta existente al seleccionarla explícitamente. Al crear, un nombre parecido pregunta "¿Te referías a...?" y permite continuar; un nombre exacto existente se rechaza. Los torneos con hándicap desactivado generan emparejamientos sin piedras.
- Solo los administradores pueden crear, restaurar o eliminar copias de seguridad. No se puede eliminar, desactivar ni degradar al último administrador activo.
- Copias programadas: ejecuta `python scripts/scheduled_backup.py` desde el Programador de tareas de Windows o cron, por ejemplo una vez al día. Cada ejecución crea una copia, verifica que una copia de trabajo pueda migrarse y restaurarse, y solo entonces elimina las copias programadas que superen `SCHEDULED_BACKUP_KEEP`. Esta tarea nunca elimina copias manuales, previas a una importación ni previas a una restauración. El script termina con estado 1 si la verificación falla.
- La sustitución completa por XLSX primero muestra los conteos actuales y los importables, y exige confirmación explícita. Antes de reemplazar los datos crea una copia SQLite verificada; si la importación falla, conserva los datos anteriores.
- `/admin/settings` permite ajustar los límites de inicio de sesión, la caducidad de recuperación y el formato del nombre de los jugadores (`Apellido, Nombre` por defecto, `Nombre Apellido` o una plantilla personalizada). Los nombres se muestran al vuelo según el formato; los datos almacenados no cambian. Cada jugador puede tener un nombre personalizado opcional, editable por operadores y administradores. Nombre y apellido no pueden contener comas. En producción usa HTTPS, contraseñas únicas y secretos solo en variables de entorno.
- Las cuentas autenticadas pueden cargar archivos SGF sin vincularlos. Los administradores, directores y operadores pueden vincularlos o desvincularlos; solo los administradores pueden eliminar archivos.

## Hoja de ruta del proyecto

La hoja de ruta está en [FUTURE_FEATURES.md](FUTURE_FEATURES.md). La reconciliación de importaciones, los payloads tipados de OpenGotha, los filtros de auditoría, las mejoras de perfiles, la eliminación explícita de torneos, el tratamiento de ratings, perfiles y listados según el estado de los torneos y las clasificaciones por país, región y grupos ya están implementados. El trabajo pendiente incluye mejorar el tema oscuro de BesoGo, torneos Pairgo y permisos por país.

## Desarrollo

La aplicación renderiza las guías desde Markdown al solicitarlas; no es necesario generar HTML para ejecutarla ni para probarla. Para exportar copias HTML estáticas después de cambiar las fuentes:

```powershell
python scripts/build_help_html.py
```

Ejecuta la suite de regresión desde la raíz del proyecto:

```powershell
pytest -q
```

### Organización del código

La importación CSV normaliza los encabezados sin distinguir mayúsculas ni espacios exteriores, y rechaza encabezados duplicados después de normalizarlos. `scripts/export_players.py` crea `exports/` si todavía no existe.

Las rutas administrativas están separadas por dominio entre `routes/admin_tournaments.py`, `routes/admin_matches.py`, `routes/admin_players.py` y `routes/admin_users.py`. La lógica de torneos está separada por responsabilidad entre `services/tournament_gotha.py`, `services/tournament_participants.py`, `services/tournament_pairing.py`, `services/tournament_matches.py` y `services/tournament_standings.py`. Las traducciones y la selección de idioma viven en `services/i18n.py`, mientras que los helpers puros de gráficos de rating viven en `services/chart_service.py`; `services/common.py` conserva exports de compatibilidad para los imports existentes. `services/tournament_service.py` se conserva como fachada de compatibilidad. Los ejemplos XML de OpenGotha usados por los tests están versionados y anonimizados en `tests/fixtures/opengotha/`; no se necesitan archivos locales en `uploads/` ni guías generadas para ejecutar `pytest -q`.

### Instalación en hosting Linux

Usa Python 3.10 o posterior y crea un entorno virtual nuevo antes de instalar:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=Pillow -r requirements.txt
```

Si ese comando indica que no existe una rueda compatible de Pillow, la versión de Python,
la arquitectura o la distribución Linux seleccionada por el hosting no es compatible.
Selecciona Python 3.10+ x86_64 en el panel del hosting; no intentes compilar Pillow sin
las bibliotecas de desarrollo de Python, JPEG, zlib y freetype del sistema.

Las ruedas de Babel y pycountry (Python puro, válidas para Linux y Windows) se incluyen en
`vendor/wheels/`. Si el hosting no puede descargarlas de PyPI, descomprime cada `.whl` (es un
archivo zip) en el directorio `site-packages` del entorno virtual, de modo que queden las carpetas
del paquete y su `*.dist-info`; después `pip install -r requirements.txt` las reconoce como instaladas.

Para investigar un 404 sin SSH, ejecuta `scripts/dev_only/diagnose_hosting.py` desde
el ejecutor de scripts Python de cPanel con el entorno virtual de la aplicación. Descarga
`temp/deployment-diagnostic.json` mediante el administrador de archivos y elimina el informe
del servidor. Registra imports reales, el intérprete, directivas de rutas y errores de `.htaccess`,
permisos y enlaces simbólicos, la sintaxis del archivo de inicio y una prueba de la ruta estática
bajo `/glicko`, sin ejecutar migraciones de la base de datos.
No incluye valores de variables de entorno; aun así, mantén el informe privado.
El ejecutor puede tener un entorno distinto al de Passenger: compara su ejecutable con
`PassengerPython` y revisa por separado la URL de la aplicación y la raíz documental en cPanel.
Un 404 de Joomla/PHP en la URL de la aplicación apunta al enrutamiento del sitio principal,
no a una ruta Flask; revisa el dominio, el registro de Passenger y las reglas de reescritura
del sitio principal antes de cambiar rutas o limpiar repetidamente las cachés.
Para probar Passenger sin dependencias, sube temporalmente
`scripts/dev_only/passenger_probe.py` a la raíz de la aplicación en el servidor y selecciona
`passenger_probe.py` como archivo de inicio, con punto de entrada `application`.
Tras guardar y reiniciar, la URL debe devolver `GLICKO-PASSENGER-PROBE-20261018`.
Si sigue devolviendo Joomla, solicita al hosting que revise el mapeo activo de nginx/Apache
y el montaje de Passenger. Después restaura `passenger_wsgi.py`, guarda, reinicia y elimina
el archivo de prueba subido; no lo dejes como aplicación de producción.
Para probar la inicialización real, respalda primero la base de datos de producción y ejecuta
`scripts/dev_only/check_passenger_startup.py` desde cPanel. Conserva
`diagnose_hosting.py` junto al script. A diferencia del diagnóstico de solo lectura,
ejecuta `passenger_wsgi.py`, incluidas las migraciones y la inicialización normales,
y solicita la página principal internamente. Descarga el informe privado
`temp/passenger-startup-check.json` y elimínalo del servidor. Un resultado exitoso
no verifica el mapeo del servidor web.
El `passenger_wsgi.py` desplegado debe importar `create_app` de `app` y exponer
`application = create_app(auto_init=True)`. Un wrapper generado que llama
`imp.load_source('wsgi', 'passenger_wsgi.py')` desde ese mismo archivo se carga
recursivamente y no puede iniciar. Tras cambiar los ajustes de inicio en cPanel,
comprueba que el archivo desplegado coincida con el repositorio; una prueba de sintaxis
no detecta esta recursión. Usa el intérprete configurado en cPanel en lugar de
añadir una ruta de entorno virtual de Windows en Linux.

Si Apache no carga `mod_passenger`, cualquier directiva `Passenger*` en `.htaccess`
provoca un error 500 que el sitio padre puede mostrar como un 404 de Joomla. Como
solución temporal con la misma URL, sube `glicko.cgi` y `cgi_bridge.py` a la raíz de
la aplicación (con finales de línea LF), da permisos 755 a `glicko.cgi` y ajusta su primera línea al Python del
entorno virtual de cPanel. Detén la aplicación en el selector de Python para que cPanel
retire el bloque de Passenger y añade el contenido de `cgi_fallback.htaccess` al
`.htaccess` de la aplicación. El puente lee las variables de entorno configuradas en el
selector, ejecuta las migraciones una vez por cada `app.py` desplegado y sirve cada
petición en un proceso nuevo, por lo que es más lento que Passenger. Cuando Passenger
vuelva a funcionar, elimina ese bloque y `glicko.cgi`, e inicia de nuevo la aplicación.

Las pruebas cubren ratings y gráficos, filtros de jugadores, soporte de idiomas, respaldos, migraciones de torneos, emparejamientos, clasificación, compatibilidad con OpenGotha, moderación de resultados y páginas públicas de torneos.

La cobertura de pruebas también incluye la biblioteca SGF, la sincronización de sus metadatos, la reparación de enlaces faltantes, sus permisos y la restauración desde copias de seguridad.

La funcionalidad de ordenación, filtros y búsqueda consistente ya está entregada y validada en las páginas de jugadores, partidas y torneos.

## Licencia y atribución
Glicko-db fue originalmente desarrollado para la comunidad de Go en Colombia por Juan Felipe Samper en 2026. 

El sistema [Glicko-2](https://www.glicko.net/glicko/glicko2.pdf) fue publicado por Mark E. Glickman en 2022 al dominio público. La implementación en Python es ©2009 Ryan Kirkman y BesoGo es ©2015-2018 Ye Wang. Ambas se distribuyen bajo la [licencia MIT](./static/vendor/besogo/LICENSE).
