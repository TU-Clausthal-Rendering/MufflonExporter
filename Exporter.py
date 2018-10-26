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
        aperture = camera.gpu_dof.fstop
        if aperture == 128.0:
            cameraType = "pinhole"
            dataDictionary['cameras'][camera.name] = collections.OrderedDict()
            dataDictionary['cameras'][camera.name]['type'] = cameraType
            fov = camera.angle * 180 / 3.141592653589793  # convert rad to degree
            dataDictionary['cameras'][camera.name]['fov'] = fov
        else:
            cameraType = "focus"
            dataDictionary['cameras'][camera.name] = collections.OrderedDict()
            dataDictionary['cameras'][camera.name]['type'] = cameraType
            dataDictionary['cameras'][camera.name]['focalLength'] = camera.lens
            dataDictionary['cameras'][camera.name]['chipHeight'] = camera.sensor_height
            dataDictionary['cameras'][camera.name]['focusDistance'] = camera.dof_distance
            dataDictionary['cameras'][camera.name]['aperture'] = aperture
    elif camera.type == "ORTHO":
        cameraType = "ortho"
        dataDictionary['cameras'][camera.name] = collections.OrderedDict()
        dataDictionary['cameras'][camera.name]['type'] = cameraType
        orthoWidth = camera.ortho_scale
        dataDictionary['cameras'][camera.name]['width'] = orthoWidth
        orthoHeight = scn.render.resolution_y / scn.render.resolution_x * orthoWidth  # get aspect ratio via resolution
        dataDictionary['cameras'][camera.name]['height'] = orthoHeight
    else:
        print("Skipping unsupported camera type: \"%s\" from: \"%s\"" % camera.type, camera.name)
        continue
    cameraPath = []
    viewDirectionPath = []
    upPath = []
    if camera.animation_data:  # TODO Check if this works
        x_curve = camera.animation_data.action.fcurves.find('location', index=0)
        y_curve = camera.animation_data.action.fcurves.find('location', index=1)
        z_curve = camera.animation_data.action.fcurves.find('location', index=2)
        for f in range(scn.frame_start, scn.frame_end):
            x_pos = x_curve.evaluate(f)
            y_pos = y_curve.evaluate(f)
            z_pos = z_curve.evaluate(f)
            cameraPath.append([x_pos, z_pos , y_pos])
    else:  # for not animated Cameras
        cameraPath.append([cameraObject.location.x, cameraObject.location.z, cameraObject.location.y])
        viewDirection = cameraObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
        viewDirectionPath.append([viewDirection.x, viewDirection.z, viewDirection.y])
        up = cameraObject.matrix_world.to_quaternion() * Vector((0.0, 1.0, 0.0))
        upPath.append([up.x, up.z, up.y])
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
        dataDictionary['lights'][lamp.name]['position'] = [lampObject.location.x, lampObject.location.z, lampObject.location.y]
        dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
    elif lamp.type == "SUN":
        lightType = "directional"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
        dataDictionary['lights'][lamp.name]['type'] = lightType
        viewDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
        dataDictionary['lights'][lamp.name]['direction'] = [viewDirection.x, viewDirection.z, viewDirection.y]
        dataDictionary['lights'][lamp.name]['radiance'] = "PlaceHolder"  # TODO
        dataDictionary['lights'][lamp.name]['scale'] = "PlaceHolder"  # TODO
    elif lamp.type == "SPOT":
        lightType = "spot"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
        dataDictionary['lights'][lamp.name]['type'] = lightType
        dataDictionary['lights'][lamp.name]['position'] = [lampObject.location.x, lampObject.location.z, lampObject.location.y]
        lightDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
        dataDictionary['lights'][lamp.name]['direction'] = [lightDirection.x, lightDirection.z, lightDirection.y]
        dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
        dataDictionary['lights'][lamp.name]['exponent'] = 4.0
        dataDictionary['lights'][lamp.name]['width'] = lamp.spot_size / 2
        dataDictionary['lights'][lamp.name]['falloffStart'] = lamp.spot_size / 2
    else:
        print("Skipping unsupported lamp type: \"%s\" from: \"%s\"" % lamp.type, lamp.name)
        continue
    # TODO envmap, goniometric

# Materials

# Scenarios

print(json.dumps(dataDictionary, indent=4))

file = open(os.path.splitext(bpy.data.filepath)[0] + ".json", 'w')
file.write(json.dumps(dataDictionary, indent=4))
file.close()
