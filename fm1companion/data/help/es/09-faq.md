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

**Algo se ha bloqueado.**
La app muestra entonces un diálogo con *Guardar informe de errores*. El informe contiene registros, versiones y
ajustes sin rutas personales – tú decides si lo compartes. Para crear uno tú mismo: Ayuda ›
Crear informe de errores.

**¿Cómo recupero el aspecto antiguo?**
Ver › Tema › *Estilo del sistema (KDE/Breeze)*. Iconos: Ajustes › Apariencia › *Tema del sistema (Breeze/Papirus)*.
