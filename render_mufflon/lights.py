import bpy
import math
import mathutils
from ctypes import *
from enum import Enum
from . import (bindings, util)
from .bindings import (DllInterface, LightType)
from .util import *

# Defines if the emission node has blackbody, gioniometric, or no custom input
class EmissionType(Enum):
    BLACKBODY = 1
    GONIOMETRIC = 2
    NONE = 3

# Checks what kind of emission an emission node is. Options are current black-body,
# goniometric (which is every color input that is not black-body), and none (which
# means we use the 'Color' input directly from the emission node)
def get_emission_type(emissionNode):
    # Check for blackbody input node
    colorInput = emissionNode.inputs['Color']
    if len(colorInput.links) > 0:
        colorInputNode = colorInput.links[0].from_node
        if colorInputNode.bl_idname == 'ShaderNodeBlackbody':
            return EmissionType.BLACKBODY
        else: # We simply assume that any other input means goniometric
            return EmissionType.GONIOMETRIC
    return EmissionType.NONE

def find_light_output_node(light):
    if not (hasattr(light, 'node_tree') or hasattr(light.node_tree, 'nodes')):
        raise Exception("%s is not a node-based light"%(material.name))
    for node in light.node_tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputLight' and node.is_active_output:
            return node
    return None

# Reads the color or temperature of the light source
def get_light_color_or_temperature(light, emissionType, emissionNode):
    if emissionType == EmissionType.BLACKBODY:
        return get_scalar_def_only_input(emissionNode.inputs['Color'].links[0].from_node, 'Temperature')
    elif emissionType == EmissionType.GONIOMETRIC:
        # TODO
        return 0.0
    else:
        color = get_scalar_def_only_input(emissionNode, 'Color')
        return [light.color.r * color[0], light.color.g * color[1], light.color.b * color[2]]
    
def get_point_light_intensity(light, emissionNode):
    emissionType = get_emission_type(emissionNode)
    if emissionType == EmissionType.GONIOMETRIC:
        raise Exception("light '%s' is detected to be goniometric (color input other than 'Blackbody'); this is not supported yet!"%(light.name))
    # TODO: temperature!
    scale = light.energy * get_scalar_def_only_input(emissionNode, 'Strength') / (4.0 * math.pi)
    flux = get_light_color_or_temperature(light, emissionType, emissionNode)
    return to_vec3([scale * flux[0], scale * flux[1], scale * flux[2]])

def get_spot_light_intensity(light, emissionNode):
    emissionType = get_emission_type(emissionNode)
    if emissionType == EmissionType.GONIOMETRIC:
        raise Exception("spot light '%s' does not support non-scalar or non-black-body emission!"%(light.name))
    # TODO: temperature!
    intensity = get_light_color_or_temperature(light, emissionType, emissionNode)
    # We have to convert the flux as defined by blender to the peak intensity
    # To retain the same brightness impression, blender always distributes the flux
    # on the unit sphere and culls the parts which are not in the spot, leaving
    # an equal intensity when changing the spot size
    flux = light.energy * get_scalar_def_only_input(emissionNode, 'Strength')
    scale = flux / (4.0 * math.pi)
    return to_vec3([scale * intensity[0], scale * intensity[1], scale * intensity[2]])
    
def get_directional_light_radiance(light, emissionNode):
    emissionType = get_emission_type(emissionNode)
    if emissionType == EmissionType.GONIOMETRIC:
        raise Exception("spot light '%s' does not support non-scalar or non-black-body emission!"%(light.name))
    # TODO: temperature!
    radiance = get_light_color_or_temperature(light, emissionType, emissionNode)
    scale = light.energy * get_scalar_def_only_input(emissionNode, 'Strength')
    return to_vec3([scale * radiance[0], scale * radiance[1], scale * radiance[2]])

def add_lights(interface, lamps):
    lightHdls = []
    for lampObject in lamps:
        lamp = lampObject.data
        if lamp.users == 0:
            continue
        if lamp.use_nodes and lamp.node_tree:
            # Fetch the emission node if the light uses nodes
            outputNode = find_light_output_node(lamp)
            if len(outputNode.inputs['Surface'].links) == 0:
                raise Exception("light '%s' is missing output link"%(light.name))
            emissionNode = outputNode.inputs['Surface'].links[0].from_node
            if emissionNode.bl_idname != 'ShaderNodeEmission':
                raise Exception("light '%s' does not have emission node as last output node (other nodes not yet supported!)"%(lamp.name))
            
        if lamp.type == "POINT":
            lightHdl = interface.world_add_light(lampObject.name, LightType.POINT, 1)
            if lightHdl == c_void_p(0):
                raise Exception("Failed to add light '%s'"%(lamp.name))
            lightHdls.append(lightHdl)
            
            if lamp.use_nodes and lamp.node_tree:
                intensity = get_point_light_intensity(lamp, emissionNode)
            else:
                intensity = to_vec3([lamp.energy * lamp.color.r, lamp.energy * lamp.color.g, lamp.energy * lamp.color.b])
            pos = to_vec3(flip_space(lampObject.location)) 
            if not interface.world_set_point_light_position(lightHdl, pos, 0):
                raise Exception("Failed to set light position of light '%s'"%(lamp.name))
            if not interface.world_set_point_light_intensity(lightHdl, intensity, 0):
                raise Exception("Failed to set light intensity of light '%s'"%(lamp.name))
        elif lamp.type == "SPOT":
            lightHdl = interface.world_add_light(lampObject.name, LightType.SPOT, 1)
            if lightHdl == c_void_p(0):
                raise Exception("Failed to add light '%s'"%(lamp.name))
            lightHdls.append(lightHdl)
            
            if lamp.use_nodes and lamp.node_tree:
                intensity = get_spot_light_intensity(lamp, emissionNode)
            else:
                intensity = to_vec3([lamp.energy * lamp.color.r, lamp.energy * lamp.color.g, lamp.energy * lamp.color.b])
            pos = to_vec3(flip_space(lampObject.location))
            dir = to_vec3(flip_space(lampObject.matrix_world.to_quaternion() @ mathutils.Vector((0.0, 0.0, -1.0))))
            width = lamp.spot_size / 2
            # Try to match the inner circle for the falloff (not exact, blender seems buggy):
            # https://blender.stackexchange.com/questions/39555/how-to-calculate-blend-based-on-spot-size-and-inner-cone-angle
            falloff = math.atan(math.tan(lamp.spot_size / 2) * math.sqrt(1-lamp.spot_blend))
            if not interface.world_set_spot_light_position(lightHdl, pos, 0):
                raise Exception("Failed to set light position of light '%s'"%(lamp.name))
            if not interface.world_set_spot_light_direction(lightHdl, dir, 0):
                raise Exception("Failed to set light direction of light '%s'"%(lamp.name))
            if not interface.world_set_spot_light_intensity(lightHdl, intensity, 0):
                raise Exception("Failed to set light intensity of light '%s'"%(lamp.name))
            if not interface.world_set_spot_light_angle(lightHdl, width, 0):
                raise Exception("Failed to set light angle of light '%s'"%(lamp.name))
            if not interface.world_set_spot_light_falloff(lightHdl, falloff, 0):
                raise Exception("Failed to set light falloff of light '%s'"%(lamp.name))
        elif lamp.type == "SUN":
            lightHdl = interface.world_add_light(lampObject.name, LightType.DIRECTIONAL, 1)
            if lightHdl == c_void_p(0):
                raise Exception("Failed to add light '%s'"%(lamp.name))
            lightHdls.append(lightHdl)
            
            if lamp.use_nodes and lamp.node_tree:
                radiance = get_directional_light_radiance(lamp, emissionNode)
            else:
                radiance = to_vec3([lamp.energy * lamp.color.r, lamp.energy * lamp.color.g, lamp.energy * lamp.color.b])
            dir = to_vec3(flip_space(lampObject.matrix_world.to_quaternion() @ mathutils.Vector((0.0, 0.0, -1.0))))
            if not interface.world_set_dir_light_direction(lightHdl, dir, 0):
                raise Exception("Failed to set light direction of light '%s'"%(lamp.name))
            if not interface.world_set_dir_light_irradiance(lightHdl, radiance, 0):
                raise Exception("Failed to set light irradiance of light '%s'"%(lamp.name))
            pass
        else:
            continue
    return lightHdls