# Preguntas frecuentes

**La app no encuentra mi FM-1.**
Usa un cable USB de datos, enciende el FM-1, pulsa F5. Si no sirve: [Comprobación del sistema](app://action/syscheck) –
muestra si el ordenador ve el dispositivo.

**El FM-1 se desconecta una y otra vez.**
Casi siempre es el cable, el hub o la fuente de alimentación. La comprobación del sistema detecta estos bucles en el registro del kernel y te dice
qué hacer.

**¿Puedo hacer otra cosa durante una actualización?**
Sí, pero nada que se comunique con el FM-1 (editor en el navegador, DAW), y no desenchufes el USB.

**¿La app recopila datos?**
No. Sin telemetría, sin cuentas. La app solo obtiene dos cosas automáticamente (se puede desactivar): al conectar un
FM-1, la lista de versiones de su firmware en GitHub, y al abrir el catálogo, el catálogo de awesome-fm-1
con imágenes de vista previa. Todo lo demás, solo cuando haces clic. Los informes de errores solo se guardan localmente.
Para desactivarlo: Ajustes › “buscar actualizaciones de firmware al conectar un FM-1 (consulta GitHub)” y en el catálogo en “Más”
(“Actualizar el catálogo automáticamente al abrirlo”, “Cargar imágenes de vista previa de internet”).

**¿Dónde están mis datos?**
Todo lo que es tuyo está en la carpeta principal (por defecto `~/FM-1`). **Nunca** se borra al desinstalar.

**¿Cómo borro registros antiguos y firmware descargado?**
Herramientas › *Almacenamiento y limpieza …* (o Ajustes › Almacenamiento) muestra lo que la app guarda en el
ordenador – registros, paquetes de firmware descargados, copias de paquetes instalados, herramientas de firmware,
copias del editor, imágenes del catálogo, descargas de paquetes de samples, caché web, archivos temporales e informes
de errores – con su tamaño, *Abrir carpeta* y *Borrar …* (siempre con confirmación). Tus copias de seguridad,
proyectos, samples, kits y grabaciones nunca se borran ahí, ni nada que una instalación interrumpida aún necesite (el
paquete del diario, las herramientas de firmware, el firmware original V15 más reciente para un rescate sin internet).
Mientras hay una instalación en curso no se borra nada. Los estados del Studio son tu trabajo: solo se borran uno por
uno (*Borrar un estado …*).

La **limpieza automática** (misma pestaña, activada por defecto, como máximo una vez al día) borra las partes antiguas
del registro tras 30 días, conserva las 3 copias de paquetes más recientes y las 2 versiones del editor más recientes por firmware, y borra
imágenes del catálogo, descargas de paquetes de samples y archivos temporales tras 30 días. La
[comprobación del sistema](app://action/syscheck) muestra el total.

Las descargas de firmware en `~/FM-1/Firmware` nunca se borran automáticamente (esa carpeta es tuya); en la misma
pestaña puedes activar «conservar las N descargas más recientes por firmware».

**Volcados de rescate, datos de los editores web, ajustes de las páginas, token de GitHub – ¿cómo los limpio?**
* **Volcados de rescate y registros de la herramienta** (carpeta principal › `Rettung`): misma pestaña, *Mostrar y
  borrar volcados …* lista las ejecuciones, los volcados de flash y los registros de la herramienta de rescate con fecha
  y tamaño. Son tu red de seguridad, así que la app nunca los borra por su cuenta: de uno en uno, siempre con
  confirmación – y la **copia completa más reciente de cada dispositivo** (marcada *LA MÁS RECIENTE*) pregunta una
  segunda vez.
* **Datos de los editores web** (cookies y almacenamiento local del navegador integrado): *Borrar datos de los
  editores web …*. Los editores pueden guardar ahí tus ajustes; después empiezan como en la primera visita. Si el
  navegador integrado está abierto, las cookies se borran al momento y el resto en el próximo inicio de la app.
* **Ajustes de las páginas** (catálogo, Studio, MIDI, grabación …): Ajustes › General › *Restablecer ajustes de …* –
  marca las páginas. Lo que es tuyo se queda (token de GitHub, lista de recordados, asignaciones de MIDI learn,
  soundfonts, carpetas, dispositivos).
* **Token de GitHub**: Firmware+ › Más › *Token de GitHub …* muestra si hay uno configurado (solo sus cuatro últimos
  caracteres) y tiene *Quitar token*.
* **Olvidar un dispositivo** (Ajustes › Dispositivos › *Olvidar*) también puede borrar su estado del Studio; las copias
  de seguridad y los proyectos se quedan.
* **Las descargas de paquetes de samples** ya no guardan una segunda copia: la copia git se borra en cuanto los samples
  se han copiado a `Samples` (una nueva descarga la vuelve a obtener).
* **Espejo NAS**: el espejo de copias de seguridad también borra las copias automáticas antiguas, como la carpeta de
  copias – solo archivos que la propia app copió allí y que ya borró aquí; nunca las manuales, las fijadas ni archivos
  ajenos (Copias de seguridad › Copias automáticas, *borrar también en el espejo las copias automáticas antiguas*).
* **Las lecturas largas se pueden cancelar**: obtener proyectos, leer un proyecto en *Ver y exportar* y leer el banco
  de samples de FM1 Open se detienen en el siguiente bloque – no se cambia nada en el FM-1 y lo ya guardado se queda.

**Algo se ha bloqueado.**
La app muestra entonces un diálogo con *Guardar informe de errores*. El informe contiene registros, versiones y
ajustes sin rutas personales – tú decides si lo compartes. Para crear uno tú mismo: Ayuda ›
Crear informe de errores.

**¿Cómo recupero el aspecto antiguo?**
Ver › Tema › *Estilo del sistema (KDE/Breeze)*. Iconos: Ajustes › Apariencia › *Tema del sistema (Breeze/Papirus)*.
