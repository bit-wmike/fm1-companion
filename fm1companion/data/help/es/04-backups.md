# Copias de seguridad

Una copia de seguridad contiene todo lo que el firmware entrega a través de su interfaz: proyectos, presets de usuario,
ajustes. Es el mismo formato que el del editor web del firmware.

* **Crear:** Ctrl+B o [Copias de seguridad](app://page/backups) › *Hacer copia ahora*. Opcionalmente con una nota.
* **Restaurar:** elige una copia › *Restaurar todo …*. Antes, la app crea automáticamente una
  copia de seguridad previa del estado actual, así siempre puedes volver atrás.
* **Proyectos sueltos** se pueden traer del dispositivo o de una copia en la página [Proyectos](app://page/projects).
* **¿Dónde están?** En la carpeta principal (por defecto `~/FM-1/Backups/<nombre del dispositivo>/`). De vez en cuando, copia también esta
  carpeta a una memoria USB o a la nube.
* Las copias automáticas (antes de actualizaciones) se limpian al superar el número configurado; las manuales se conservan siempre.

## ¿Qué firmwares se pueden copiar?

Las copias pasan por el protocolo de editor del firmware. La app habla tres protocolos de copia: el de
**Felucca** (Felucca, Melodee, Skiff, JIANT FM, CoralFlower, ChoralRoot y los forks de Felucca que lo conservan),
el de **SLOOP** (SLOOP, KARMA, ORBIT y los forks de SLOOP: sloopDX, Clipper, Jangada, winampFM, …) y el de **X0X**
(desde X0X 1.0.9); FM1 Open de M-VAVE tiene uno propio.

* Un firmware que dice su nombre cuando se le pregunta («FELUCCA CLIPPER 1.3.1») recibe sus propias reglas. A uno
  que responde como Felucca («FELUCCA v1.0») se le **pregunta** qué protocolo de copia habla, solo con peticiones
  de lectura: la app nunca escribe con un protocolo adivinado.
* Un firmware con un protocolo de copia propio (Optimist, Hortator) o sin ninguno recibe un mensaje claro en lugar
  de un tiempo de espera agotado: haz la copia con su propio editor.
* Una copia solo vuelve a un firmware que guarda lo mismo bajo los mismos objetos. Una copia de sloopDX en SLOOP o
  una de Saadet en Felucca acabarían en el sitio equivocado: la app se niega antes de escribir nada.
* La lista completa de los firmwares del catálogo y lo que la app hace con cada uno está en `docs/firmware-support.md`.
