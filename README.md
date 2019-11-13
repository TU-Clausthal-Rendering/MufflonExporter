# MufflonExporter

Blender exporter for Mufflon's file format.

Mufflon is the research renderer of the TU-Clausthal graphics group.

The files consist of two parts. A JSON with material, light and camera information and a *.mff file with binary mesh data. For more details see the file format documentation in the Mufflon repository.

The exporter script extends several data types in blender to allow the parametrization according to our renderer.

## Object -> Perfect Sphere (Mufflon)

In the object properties, there is a panel to switch the sphere flag on and off.
If enabled, the object will be exported as a sphere primitive which is directly supported by Mufflon.

## Material -> Outer Medium

To model boundaries between several non-trivial media, Mufflon uses dedicated media on both sides of a surface.
The material defines the inner medium.
The outer medium, defined by the panel's values, is that on the side to which the normal points.
With this distinction it is possible to render, for example, a vacuum - glass - water transition.
