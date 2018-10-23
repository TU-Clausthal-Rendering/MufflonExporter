import bpy
from mathutils import Vector
import os
import json
import collections

version = "0.1"
binary = "Path/To/Binary.bin"

scn = bpy.context.scene

dataDictionary = collections.OrderedDict()
dataDictionary['version'] = version
dataDictionary['binary'] = binary
dataDictionary['cameras'] = collections.OrderedDict()
dataDictionary['lights'] = collections.OrderedDict()
dataDictionary['materials'] = collections.OrderedDict()
dataDictionary['scenarios'] = collections.OrderedDict()

# Cameras

cameras = bpy.data.cameras

for i in range(len(cameras)):
    camera = cameras[i]
    cameraObject = bpy.data.objects[camera.name]
    cameraType = ""
    if camera.type == "PERSP":
        cameraType = "focus"
    elif camera.type == "ORTHO":
        cameraType = "ortho"
    else:
        print("Skipping unsupported camera type: \"%s\" from: \"%s\"" % camera.type, camera.name)
        continue
    cameraPath = []
    viewDirectionPath = []
    upPath = []
    if camera.animation_data: # TODO Check if this works
        x_curve = camera.animation_data.action.fcurves.find('location', index=0)
        y_curve = camera.animation_data.action.fcurves.find('location', index=1)
        z_curve = camera.animation_data.action.fcurves.find('location', index=2)
        for f in range(scn.frame_start, scn.frame_end):
            x_pos = x_curve.evaluate(f)
            y_pos = y_curve.evaluate(f)
            z_pos = z_curve.evaluate(f)
            cameraPath.append([x_pos, y_pos, z_pos])
    else: # for not animated Cameras
        cameraPath.append([cameraObject.location.x, cameraObject.location.y, cameraObject.location.z])
        viewDirection = cameraObject.matrix_world.to_quaternion() * Vector((0.0, -1.0, 0.0))
        viewDirectionPath.append([viewDirection.x, viewDirection.y, viewDirection.z])
        up = cameraObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, 1.0))
        upPath.append([up.x, up.y, up.z])
    dataDictionary['cameras'][camera.name] = collections.OrderedDict()
    dataDictionary['cameras'][camera.name]['type'] = cameraType
    dataDictionary['cameras'][camera.name]['path'] = cameraPath
    dataDictionary['cameras'][camera.name]['viewDir'] = viewDirectionPath
    dataDictionary['cameras'][camera.name]['up'] = upPath

# Lights

lamps = bpy.data.lamps
for i in range(len(lamps)):
    lamp = lamps[i]
    lampObject = bpy.data.objects[lamp.name]
    lightType = ""
    if lamp.type == "POINT":
        lightType = "point"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
        dataDictionary['lights'][lamp.name]['type'] = lightType
        dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
    elif lamp.type == "SUN":
        lightType = "directional"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
        dataDictionary['lights'][lamp.name]['type'] = lightType
        viewDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, -1.0, 0.0))
        dataDictionary['lights'][lamp.name]['direction'] = [viewDirection.x, viewDirection.y, viewDirection.z]
        dataDictionary['lights'][lamp.name]['irradiance'] = "PlaceHolder" # TODO
        dataDictionary['lights'][lamp.name]['scale'] = "PlaceHolder"  # TODO
    elif lamp.type == "SPOT":
        lightType = "spot"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
        dataDictionary['lights'][lamp.name]['type'] = lightType
        dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
        dataDictionary['lights'][lamp.name]['exponent'] = 4.0
        dataDictionary['lights'][lamp.name]['width'] = lamp.spot_size
        dataDictionary['lights'][lamp.name]['falloffStart'] = lamp.spot_size
        # TODO Parameters
    else:
        print("Skipping unsupported lamp type: \"%s\" from: \"%s\"" % lamp.type, lamp.name)
        continue
    # TODO Other parameters (pos)
    # TODO envmap, goniometric

# Materials

# Scenarios

print(json.dumps(dataDictionary, indent=4))

file = open(os.path.splitext(bpy.data.filepath)[0] + ".json", 'w')
file.write(json.dumps(dataDictionary, indent=4))
file.close()
