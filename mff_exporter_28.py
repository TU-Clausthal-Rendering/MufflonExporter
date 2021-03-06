
import bpy
import bmesh
import ctypes
from mathutils import Vector
import mathutils
import os
import json
import collections
import struct
import numpy
import math
import zlib
from collections import OrderedDict
import re
from enum import Enum
from inspect import currentframe, getframeinfo
from collections.abc import Mapping, Sequence

bl_info = {
    "name": "Mufflon Exporter",
    "description": "Exporter for the custom Mufflon file format",
    "author": "Marvin, Johannes Jendersie, Florian Bethe",
    "version": (1, 6),
    "blender": (2, 80, 0),
    "location": "File > Export > Mufflon (.json/.mff)",
    "category": "Import-Export"
}


# Brief notes; open issues are:
# * Anisotropy currently has no way of being represented in the blender internal materials and is thus not exported
# * Conversely, complex fresnel currently has no way of being represented in Cycle nodes and is thus not converted
# * Alpha blending has to be plugged at the node closest to the material output as a blend of transparent + anything w. blend input from texture 'Alpha'
# * Baking procedural textures ignores any UV input and uses a fixed resolution
# * All and any texture slots occupied prior to node->internal conversion will be vacated
# * Doesn't support many many node setups such as groups, ramping, math etc.; overall relatively brittle, but good enough to reduce material change complexity
# * Toggles the material's use_nodes for successful conversions


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

def find_material_output_node(material):
    if not (hasattr(material, 'node_tree') or hasattr(material.node_tree, 'nodes')):
        raise Exception("%s is not a node-based material"%(material.name))
    for node in material.node_tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputMaterial' and node.is_active_output:
            return node
    return None

def find_light_output_node(light):
    if not (hasattr(light, 'node_tree') or hasattr(light.node_tree, 'nodes')):
        raise Exception("%s is not a node-based light"%(material.name))
    for node in light.node_tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputLight' and node.is_active_output:
            return node
    return None

def find_world_output_node(tree):
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputWorld' and node.is_active_output:
            return node
    return None


# Delete all material keys which might exist due to a change of the material
materialKeys = ["alpha", "displacement", "type", "albedo", "roughness", "ndf", "absorption", "radiance",
                "scale", "factorA", "factorB", "layerA", "layerB", "layerReflection", "layerRefraction",
                "clearcoat", "clearcoatRoughness", "sheen", "sheenTint", "specularTint", "specTrans",
                "scatterDistance", "metallic", "baseColor", "ior", "shadowingModel", "anisotropic", "outerMedium"]
def remove_known_matkeys(json_material):
    for key in materialKeys:
        json_material.pop(key, None)


def bake_texture_node(node, material, outputName, isScalar, bakeTextures):
    fileName = material.name + "_" + node.name + ".png"
    filePath = "//baked_textures//" + fileName

    # If we don't want to (re-)bake textures just return the file path so it can be written or baked later
    if not bakeTextures:
        return filePath

    print("Baking node '%s' of material '%s'"%(node.name, material.name))
    # TODO: how to allow resolutions other than 1024x1024
    # TODO: don't bake textures multiple times
    bakeWidth = 1024
    bakeHeight = 1024
    
    # Remember what object to select later
    prevActiveObject = bpy.context.view_layer.objects.active
    # Add a temporary plane
    if bpy.ops.object.select_all.poll():
        bpy.ops.object.select_all(action='DESELECT')
    planeMesh = bpy.data.meshes.new("TemporaryPlane")
    plane = bpy.data.objects.new("TemporaryPlaneObj", planeMesh)
    bpy.context.scene.collection.objects.link(plane)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = plane
    plane.select_set(True)
    planeMesh = bpy.context.object.data
    bm = bmesh.new()
    v0 = bm.verts.new((-1, -1, 0))
    v1 = bm.verts.new((1, -1, 0))
    v2 = bm.verts.new((1, 1, 0))
    v3 = bm.verts.new((-1, 1, 0))
    face = bm.faces.new((v0, v1, v2, v3))
    bm.faces.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.new()
    face.loops[0][uv_layer].uv = (0, 0)
    face.loops[1][uv_layer].uv = (1, 0)
    face.loops[2][uv_layer].uv = (1, 1)
    face.loops[3][uv_layer].uv = (0, 1)
    bm.to_mesh(planeMesh)
    bm.free()
    
    # Set the material as the sole one to the plane
    plane.data.materials.append(material)
    
    # Remember the links of this node and the output node
    outputNode = find_material_output_node(material)
    outNodeLinks = []
    texNodeLinks = []
    for link in material.node_tree.links:
        if link.from_node == node:
            texNodeLinks.append([link.to_socket, link.from_socket])
            material.node_tree.links.remove(link)
        elif link.to_node == outputNode:
            outNodeLinks.append([link.to_socket, link.from_socket])
            material.node_tree.links.remove(link)
    
    # Add an emissive shader and connect them
    emissiveNode = material.node_tree.nodes.new('ShaderNodeEmission')
    imageNode = material.node_tree.nodes.new('ShaderNodeTexImage')
    if isScalar:
        texEmissiveLink = material.node_tree.links.new(emissiveNode.inputs['Strength'], node.outputs[outputName])
    else:
        texEmissiveLink = material.node_tree.links.new(emissiveNode.inputs['Color'], node.outputs[outputName])
    emissiveOutputLink = material.node_tree.links.new(outputNode.inputs['Surface'], emissiveNode.outputs['Emission'])
    
    # Create the directory for the image if necessary
    imageFolder = os.path.join(os.path.abspath('//'), "baked_textures")
    if not os.path.exists(imageFolder):
        os.makedirs(imageFolder)
        
    # Bake the stuff
    bakeImage = bpy.data.images.new("TempBakeImage", width=bakeWidth, height=bakeHeight, alpha=True)
    bakeImage.alpha_mode = 'STRAIGHT'
    bakeImage.filepath = filePath
    bakeImage.file_format = 'PNG'
    imageNode.image = bakeImage
    material.node_tree.nodes.active = imageNode
    bpy.ops.object.bake(type='EMIT', pass_filter={'NONE'}, use_clear=True, width=bakeWidth, height=bakeHeight)
    bakeImage.save()
    
    # Remove temporary nodes and links
    material.node_tree.links.remove(texEmissiveLink)
    material.node_tree.links.remove(emissiveOutputLink)
    material.node_tree.nodes.remove(emissiveNode)
    # Re-add the old links
    for link in outNodeLinks:
        material.node_tree.links.new(link[1], link[0])
    for link in texNodeLinks:
        material.node_tree.links.new(link[1], link[0])
    
    # Cleanup image and material
    bpy.data.images.remove(bakeImage)
    # Cleanup temporary plane
    bpy.context.scene.collection.objects.unlink(plane)
    #bpy.ops.object.select_all(action='DESELECT')
    #plane.select_set(True)
    #bpy.ops.object.delete(use_global=True, confirm=False)
    planeMesh.user_clear()
    bpy.data.meshes.remove(planeMesh)
    
    # Restore object selection
    bpy.context.view_layer.objects.active = prevActiveObject
    bpy.context.view_layer.update()
    
    return "baked_textures/" + fileName

def property_array_to_color(prop_array):
    return [ prop_array[0], prop_array[1], prop_array[2] ]

def get_image_input(material, to_node, from_node, targetInputName, isScalar, bakeTextures):
    if from_node.bl_idname == 'ShaderNodeTexImage':
        return from_node.image.filepath.replace("//", "")
    else:
        # Find out what socket we take things from
        fromSocketName = to_node.inputs[targetInputName].links[0].from_socket.name
        return bake_texture_node(from_node, material, fromSocketName, isScalar, bakeTextures)

def get_color_input(material, node, inputName, bakeTextures):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return property_array_to_color(input.default_value)
    else:
        return get_image_input(material, node, input.links[0].from_node, inputName, False, bakeTextures)

def get_scalar_input(material, node, inputName, bakeTextures):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return input.default_value
    else:
        # TODO: support for converters!
        return get_image_input(material, node, input.links[0].from_node, inputName, True, bakeTextures)

def get_scalar_def_only_input(node, inputName):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return input.default_value
    else:
        raise Exception("non-value where only value is expected (node '%s.%s')"%(node.name, inputName))

# Transform an RGB [0,1]^3 color attribute into a physical absorption value [0,∞]
def get_mapped_absorption(color):
    return [ max(0, 1 / (1e-10 + color[0]) - 1), max(0, 1 / (1e-10 + color[1]) - 1), max(0, 1 / (1e-10 + color[2]) - 1) ]

def get_microfacet_distribution(self, materialName, node):
    if node.distribution == 'SHARP':
        # Doesn't matter since there's gonna be roughness 0
        return 'GGX'
    elif node.distribution == 'BECKMANN':
        return 'BS'
    elif node.distribution == 'MULTI_GGX':
        # TODO: uhh do we have a parameter for this?
        return 'GGX'
    elif node.distribution != 'GGX':
        self.report({'WARNING'}, ("Material '%s': unknown microfacet distribution; defaulting to GGX (node '%s')"%(materialName, node.name)))
    return 'GGX'

def write_emissive_node(self, material, node):
    dict = collections.OrderedDict()
    dict['type'] = 'emissive'
    # Check if the color input specifies a color temperature
    emissionType = get_emission_type(node)
    if emissionType == EmissionType.BLACKBODY:
        colorNode = node.inputs['Color'].links[0].from_node
        dict['temperature'] = get_scalar_def_only_input(colorNode, 'Temperature')
    elif emissionType == EmissionType.GONIOMETRIC:
        raise Exception("material '%s' has goniometric emission type, which is not supported!"%(material.name))
    else:
        dict['radiance'] = get_color_input(material, node, 'Color', self.bake_textures)
    scale = get_scalar_def_only_input(node, 'Strength')
    dict['scale'] = [ scale, scale, scale ]
    return dict
    
def write_diffuse_node(self, material, node):
    dict = collections.OrderedDict()

    if len(node.inputs['Roughness'].links) == 0:
        # Differentiate between Lambert and Oren-Nayar
        dict['albedo'] = get_color_input(material, node, 'Color', self.bake_textures)
        if node.inputs['Roughness'].default_value == 0.0:
            # Lambert
            dict['type'] = 'lambert'
        else:
            # Oren-Nayar
            dict['type'] = 'orennayar'
            dict['roughness'] = get_scalar_def_only_input(node, 'Roughness')
    else:
        raise Exception("non-value for diffuse roughness (node '%s')"%(node.name))
    return dict

def write_torrance_node(self, material, node):
    dict = collections.OrderedDict()
    dict['type'] = 'torrance'
    dict['albedo'] = get_color_input(material, node, 'Color', self.bake_textures)
    if node.distribution == 'SHARP':
        dict['roughness'] = 0.0
    else:
        dict['roughness'] = get_scalar_input(material, node, 'Roughness', self.bake_textures)
    # To match the output of cycles we square the roughness here!
    if not isinstance(dict['roughness'], str):
        dict['roughness'] *= dict['roughness']
    dict['ndf'] = get_microfacet_distribution(self, material.name, node)
    # TODO: shadowing model
    dict['shadowingModel'] = 'vcavity'
    
    # Check for anisotropy
    if node.bl_idname == 'ShaderNodeBsdfAnisotropic':
        # TODO: rotation of anisotropy!
        # TODO: what to do about normal/tangent?
        # For a mapping of the anisotropy see https://developer.blender.org/diffusion/B/browse/master/intern/cycles/kernel/shaders/node_anisotropic_bsdf.osl
        anisotropy = min(max(get_scalar_def_only_input(node, 'Anisotropy'), -0.99), 0.99)
        if not isinstance(dict['roughness'], str):
            if anisotropy < 0:
                dict['roughness'] = [ dict['roughness'] / (1 + anisotropy), dict['roughness'] * (1 + anisotropy) ]
            else:
                dict['roughness'] = [ dict['roughness'] * (1 - anisotropy), dict['roughness'] / (1 - anisotropy) ]
        if len(node.inputs['Rotation'].links) > 0 or node.inputs['Rotation'].default_value != 0.0:
            self.report({'WARNING'}, ("Material '%s': Non-zero anisotropic rotation can currently not be converted properly"%(material.name)))
        if len(node.inputs['Normal'].links) > 0:
            self.report({'WARNING'}, ("Material '%s': Non-zero normal input is currently ignored"%(material.name)))
        if len(node.inputs['Tangent'].links) > 0:
            self.report({'WARNING'}, ("Material '%s': Non-zero tangent input is currently ignored"%(material.name)))
    
    return dict

def write_walter_node(self, material, node):
    dict = collections.OrderedDict()
    dict['type'] = 'walter'
    dict['absorption'] = get_color_input(material, node, 'Color', self.bake_textures)
    # Check if the 'color' is a string or none (which means no inverting possible)
    # TODO: write 'color' out instead of absorption (since inverting a texture is difficult/expensive)
    if (dict['absorption'] is not None) and not isinstance(dict['absorption'], str):
        dict['absorption'] = get_mapped_absorption(dict['absorption'])
    if node.distribution == 'SHARP':
        dict['roughness'] = 0.0
    else:
        dict['roughness'] = get_scalar_input(material, node, 'Roughness', self.bake_textures)
    # To match the output of cycles we square the roughness here!
    dict['roughness'] *= dict['roughness']
    # TODO: shadowing model
    dict['shadowingModel'] = 'vcavity'
    dict['ndf'] = get_microfacet_distribution(self, material.name, node)
    dict['ior'] = get_scalar_def_only_input(node, 'IOR')
    return dict

def write_principled_node(self, material, node):
    dict = collections.OrderedDict()
    dict['type'] = 'disney'
    dict['baseColor'] = get_color_input(material, node, 'Base Color', self.bake_textures)
    scatterDist = property_array_to_color(get_scalar_def_only_input(node, 'Subsurface Radius'))
    scatterFact = get_scalar_def_only_input(node, "Subsurface")
    dict['scatterDistance'] = [scatterDist[0] * scatterFact, scatterDist[1] * scatterFact, scatterDist[2] * scatterFact]
    dict['metallic'] = get_scalar_def_only_input(node, 'Metallic')
    dict['roughness'] = get_scalar_def_only_input(node, 'Roughness')
    dict['anisotropic'] = get_scalar_def_only_input(node, 'Anisotropic')
    dict['specTrans'] = get_scalar_def_only_input(node, 'Transmission')
    dict['ior'] = get_scalar_def_only_input(node, 'IOR')
    dict['specularTint'] = get_scalar_def_only_input(node, 'Specular Tint')
    dict['sheen'] = get_scalar_def_only_input(node, 'Sheen')
    dict['sheenTint'] = get_scalar_def_only_input(node, 'Sheen Tint')
    dict['clearcoat'] = get_scalar_def_only_input(node, 'Clearcoat')
    dict['clearcoatRoughness'] = get_scalar_def_only_input(node, 'Clearcoat Roughness')
    if len(node.inputs['Anisotropic Rotation'].links) > 0 or node.inputs['Anisotropic Rotation'].default_value != 0.0:
        self.report({'WARNING'}, ("Material '%s': Non-zero anisotropic rotation is currently ignored"%(material.name)))
    if node.inputs['Subsurface Color'].default_value != dict['baseColor']:
        self.report({'WARNING'}, ("Material '%s': subsurface color differring from base color is currently ignored"%(material.name)))
    if len(node.inputs['Normal'].links) > 0:
        self.report({'WARNING'}, ("Material '%s': Non-zero normal input is currently ignored"%(material.name)))
    # TODO: warn about other ignored inputs (clearcoat normal, tangent, emission, alpha, subsurface method, distribution, transmission roughness)
    return dict

def write_nonrecursive_node(self, material, node):
    # TODO: support for principled BSDF?
    if node.bl_idname == 'ShaderNodeBsdfDiffuse':
        return write_diffuse_node(self, material, node)
    elif node.bl_idname == 'ShaderNodeBsdfGlossy' or node.bl_idname == 'ShaderNodeBsdfAnisotropic':
        return write_torrance_node(self, material, node)
    elif node.bl_idname == 'ShaderNodeBsdfGlass' or node.bl_idname == 'ShaderNodeBsdfRefraction':
        return write_walter_node(self, material, node)
    elif node.bl_idname == 'ShaderNodeEmission':
        return write_emissive_node(self, material, node)
    elif node.bl_idname == 'ShaderNodeBsdfPrincipled':
        return write_principled_node(self, material, node)
    else:
        # TODO: allow recursion? Currently not supported by our renderer
        raise Exception("invalid mix-shader input (node '%s')"%(node.name))

def write_glass_node(self, material, node):
    dict = collections.OrderedDict()
    # First check if the color has texture input, in which case we can't use the full microfacet model
    if len(node.inputs['Color'].links) > 0:
        raise Exception("glass cannot have non-value color since absorption must not be a texture (node '%s')"%(node.name))
    else:
        dict = write_walter_node(self, material, node)
        dict['type'] = 'microfacet'
    return dict

def write_mix_node(self, material, node, hasAlphaAlready):
    dict = collections.OrderedDict()
    if len(node.inputs[1].links) == 0 or len(node.inputs[2].links) == 0:
        raise Exception("missing mixed-shader input (node '%s')"%(node.name))
    nodeA = node.inputs[1].links[0].from_node
    nodeB = node.inputs[2].links[0].from_node
    # First check if it's blend or fresnel
    if len(node.inputs['Fac'].links) == 0 or node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeValue':
        # Blend
        dict['type'] = 'blend'
        dict['layerA'] = write_nonrecursive_node(self, material, nodeA)
        dict['layerB'] = write_nonrecursive_node(self, material, nodeB)
        # Check validity
        if not ((dict['layerA']['type'] == 'lambert' and dict['layerB']['type'] == 'emissive') or
                (dict['layerA']['type'] == 'emissive' and dict['layerB']['type'] == 'lambert') or
                (dict['layerA']['type'] == 'lambert' and dict['layerB']['type'] == 'torrance') or
                (dict['layerA']['type'] == 'torrance' and dict['layerB']['type'] == 'lambert') or
                (dict['layerA']['type'] == 'walter' and dict['layerB']['type'] == 'torrance') or
                (dict['layerA']['type'] == 'torrance' and dict['layerB']['type'] == 'walter')):
            raise Exception("invalid shader blend combination %s with %s (node %s)"%(nodeA.bl_idname, nodeB.bl_idname, node.name))
        # Emissive materials don't get blended, they're simply both
        if (nodeA.bl_idname == 'ShaderNodeBsdfDiffuse' and nodeB.bl_idname == 'ShaderNodeEmission') or (nodeB.bl_idname == 'ShaderNodeBsdfDiffuse' and nodeA.bl_idname == 'ShaderNodeEmission'):
            dict['factorB'] = 1.0
            dict['factorA'] = 1.0
        elif len(node.inputs['Fac'].links) > 0 and node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeValue':
            dict['factorB'] = node.inputs['Fac'].links[0].from_node.outputs[0].default_value
            dict['factorA'] = 1 - dict['factorB']
        else:
            dict['factorB'] = node.inputs['Fac'].default_value
            dict['factorA'] = 1 - dict['factorB']
    elif node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeFresnel' or node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeLayerWeight':
        # Fresnel
        # Get the IOR
        # TODO: how to realize complex IOR with nodes? Arghhh
        # Layer weights are a bit weird: first off there are two possibilities (only one of which is valid), secondly
        # the actual (front-facing) IOR is defined as 1 / (1 - blend)
        facNode = node.inputs['Fac'].links[0].from_node
        if facNode.bl_idname == 'ShaderNodeLayerWeight':
            if node.inputs['Fac'].links[0].from_socket.identifier == 'Facing':
                raise Exception("cannot use 'Facing' output from layer weight for mixing shaders (node '%s')"%(node.name))
            fresnelIor = 1.0 / (1.0 - max(get_scalar_def_only_input(facNode, 'Blend'), 0.001))
        else:
            fresnelIor = get_scalar_def_only_input(facNode, 'IOR')
        
        if len(facNode.inputs['Normal'].links) > 0:
            self.report({'WARNING'}, ("Material '%s': Non-zero normal input is currently ignored"%(material.name)))
        
        # Check if we have a full microfacet model
        if (nodeA.bl_idname == 'ShaderNodeBsdfGlossy' or nodeA.bl_idname == 'ShaderNodeBsdfAnisotropic') and nodeB.bl_idname == 'ShaderNodeBsdfRefraction':
            # Check if the albedo is texturized (which doesn't work with the full microfacet model)
            if len(nodeA.inputs['Color'].links) > 0:
                dict['type'] = 'fresnel'
                dict['ior'] = get_scalar_def_only_input(node.inputs['Fac'].links[0].from_node, 'IOR')
                dict['layerRefraction'] = write_nonrecursive_node(self, material, nodeA)
                dict['layerReflection'] = write_nonrecursive_node(self, material, nodeB)
            else:
                dict = write_walter_node(self, material, nodeB)
                dict['type'] = 'microfacet'
                # Warn about disagreements in roughness/absorption/ndf
                tempDict = write_torrance_node(self, material, nodeA)
                if dict['roughness'] != tempDict['roughness']:
                    self.report({'WARNING'}, ("Material '%s': microfacet layers disagree about roughness; using refractive layer's value (node '%s')"%(material.name, node.name)))
                if dict['ndf'] != tempDict['ndf']:
                    self.report({'WARNING'}, ("Material '%s': microfacet layers disagree about ndf; using refractive layer's value (node '%s')"%(material.name, node.name)))
                if dict['absorption'] != tempDict['albedo']:
                    self.report({'WARNING'}, ("Material '%s': microfacet layers disagree about absorption; using refractive layer's value (node '%s')"%(material.name, node.name)))
                if dict['shadowingModel'] != tempDict['shadowingModel']:
                    self.report({'WARNING'}, ("Material '%s': microfacet layers disagree about shadowing model; using refractive layer's value (node '%s')"%(material.name, node.name)))
                if dict['ior'] != fresnelIor:
                    self.report({'WARNING'}, ("Material '%s': refractive layer disagrees with fresnel node about the IOR; using fresnel node's value (node '%s')"%(material.name, node.name)))
                    dict['ior'] = fresnelIor
        else:
            dict['type'] = 'fresnel'
            dict['ior'] = get_scalar_def_only_input(node.inputs['Fac'].links[0].from_node, 'IOR')
            dict['layerRefraction'] = write_nonrecursive_node(self, material, nodeA)
            dict['layerReflection'] = write_nonrecursive_node(self, material, nodeB)
            # Check validity
            if not ((dict['layerReflection']['type'] == 'lambert' and dict['layerRefraction']['type'] == 'torrance') or
                    (dict['layerReflection']['type'] == 'torrance' and dict['layerRefraction']['type'] == 'lambert') or
                    (dict['layerReflection']['type'] == 'walter' and dict['layerRefraction']['type'] == 'torrance') or
                    (dict['layerReflection']['type'] == 'torrance' and dict['layerRefraction']['type'] == 'walter')):
                raise Exception("invalid shader fresnel combination %s with %s (node %s)"%(nodeA.bl_idname, nodeB.bl_idname, node.name))
            # TODO: extinction coefficient...
    elif node.inputs['Fac'].links[0].from_node.bl_idname.startswith('ShaderNodeTex'):
        # TODO: alpha from non-alpha channel texture!
        if hasAlphaAlready:
            raise Exception("material may not have more than one alpha blending (node '%s')"%(node.name))
        # We have a texture input at our hands: if it's alpha we can do smth with it!
        if node.inputs['Fac'].links[0].from_socket.name != 'Alpha':
            raise Exception("blend input for mix shader has to be 'Alpha' if it comes from a texture node (node '%s')"%(node.name))
        # Check if one of the layers is transparent and recursively call the material conversion
        if nodeA.bl_idname == 'ShaderNodeBsdfTransparent':
            if nodeB.bl_idname == 'ShaderNodeMixShader':
                dict = write_mix_node(self, material, nodeB, True)
            elif nodeB.bl_idname == 'ShaderNodeBsdfGlass':
                dict = write_glass_node(self, material, nodeB)
            else:
                dict = write_nonrecursive_node(self, material, nodeB)
        elif nodeB.bl_idname == 'ShaderNodeBsdfTransparent':
            if nodeA.bl_idname == 'ShaderNodeMixShader':
                dict = write_mix_node(self, material, nodeA, True)
            elif nodeA.bl_idname == 'ShaderNodeBsdfGlass':
                dict = write_glass_node(self, material, nodeA)
            else:
                dict = write_nonrecursive_node(self, material, nodeA)
        else:
            raise Exception("alpha blending requires one transparent node for the mix shader (node '%s')"%(node.name))
        # TODO: convert alpha channel to x channel!
        dict["alpha"] = get_image_input(material, node, node.inputs['Fac'].links[0].from_node, 'Fac', True, self.bake_textures)
    else:
        raise Exception("invalid mix-shader factor input (node '%s')"%(node.name))
        
    return dict


def write_outer_medium(self, workDictionary, material):
    if material.outer_medium.enabled:
        workDictionary['outerMedium'] = collections.OrderedDict()
        workDictionary['outerMedium']['ior'] = material.outer_medium.ior
        workDictionary['outerMedium']['absorption'] = get_mapped_absorption(material.outer_medium.transmission)


# Reads the color or temperature of the light source
def get_light_color_or_temperature(self, light, emissionType, emissionNode):
    if emissionType == EmissionType.BLACKBODY:
        if light.color.r != 1.0 or light.color.g != 1.0 or light.color.b != 1.0:
            self.report({'WARNING'}, ("Black-body light '%s' has a color scale which we cannot export; ignoring it!." % (light.name)))
        return get_scalar_def_only_input(emissionNode.inputs['Color'].links[0].from_node, 'Temperature')
    elif emissionType == EmissionType.GONIOMETRIC:
        # TODO
        return 0.0
    else:
        color = get_scalar_def_only_input(emissionNode, 'Color')
        return [light.color.r * color[0], light.color.g * color[1], light.color.b * color[2]]

def write_point_light(self, light, lampObject, emissionNode, scene, frame_range):
    emissionType = get_emission_type(emissionNode)
    if emissionType == EmissionType.GONIOMETRIC:
        raise Exception("light '%s' is detected to be goniometric (color input other than 'Blackbody'); this is not supported yet!"%(light.name))
    
    positions = []
    fluxes = [] # May also be temperature in case of blackbody
    scales = []
    # Go through all frames, accumulate the to-be-exported quantities
    for f in frame_range:
        scene.frame_set(f)
        positions.append(flip_space(lampObject.location))
        fluxes.append(get_light_color_or_temperature(self, light, emissionType, emissionNode))
        scales.append(light.energy * get_scalar_def_only_input(emissionNode, 'Strength'))
        if light.shadow_soft_size != 0.0:
            self.report({'WARNING'}, ("Point light '%s' has non-zero size, which is not supported!" % (light.name)))
    dict = collections.OrderedDict()
    dict['type'] = "point"
    dict['type'] = "point"
    dict['position'] = junction_path(positions)
    if emissionType == EmissionType.BLACKBODY:
        dict['temperature'] = junction_path(fluxes)
    else:
        dict['flux'] = junction_path(fluxes)
    dict['scale'] = junction_path(scales)
    return dict

def write_spot_light(self, light, lampObject, emissionNode, scene, frame_range):
    emissionType = get_emission_type(emissionNode)
    if emissionType == EmissionType.GONIOMETRIC:
        raise Exception("spot light '%s' does not support non-scalar or non-black-body emission!"%(light.name))
    
    positions = []
    directions = []
    intensities = [] # May also be temperature in case of blackbody
    scales = []
    widths = []
    falloffs = []
    # Go through all frames, accumulate the to-be-exported quantities
    for f in frame_range:
        scene.frame_set(f)
        positions.append(flip_space(lampObject.location))
        directions.append(flip_space(lampObject.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))))
        intensities.append(get_light_color_or_temperature(self, light, emissionType, emissionNode))
        widths.append(light.spot_size / 2)
        # Try to match the inner circle for the falloff (not exact, blender seems buggy):
        # https://blender.stackexchange.com/questions/39555/how-to-calculate-blend-based-on-spot-size-and-inner-cone-angle
        falloffs.append(math.atan(math.tan(light.spot_size / 2) * math.sqrt(1-light.spot_blend)))
        # We have to convert the flux as defined by blender to the peak intensity
        # To retain the same brightness impression, blender always distributes the flux
        # on the unit sphere and culls the parts which are not in the spot, leaving
        # an equal intensity when changing the spot size
        flux = light.energy * get_scalar_def_only_input(emissionNode, 'Strength')
        scales.append(flux / (4.0 * math.pi))
        if light.shadow_soft_size != 0.0:
            self.report({'WARNING'}, ("Spot light '%s' has non-zero size, which is not supported!" % (light.name)))
    dict = collections.OrderedDict()
    dict['type'] = "spot"
    dict['position'] = junction_path(positions)
    dict['direction'] = junction_path(directions)
    if emissionType == EmissionType.BLACKBODY:
        dict['temperature'] = junction_path(intensities)
    else:
        dict['intensity'] = junction_path(intensities)
    dict['scale'] = junction_path(scales)
    dict['width'] = junction_path(widths)
    dict['falloffStart'] = junction_path(falloffs)
    return dict
    
def write_directional_light(self, light, lampObject, emissionNode, scene, frame_range):
    emissionType = get_emission_type(emissionNode)
    if emissionType == EmissionType.GONIOMETRIC:
        raise Exception("directional light '%s' does not support non-scalar or non-black-body emission!"%(light.name))
    
    directions = []
    radiances = [] # May also be temperature in case of blackbody
    scales = []
    # Go through all frames, accumulate the to-be-exported quantities
    for f in frame_range:
        scene.frame_set(f)
        directions.append(flip_space(lampObject.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))))
        radiances.append(get_light_color_or_temperature(self, light, emissionType, emissionNode))
        scales.append(light.energy * get_scalar_def_only_input(emissionNode, 'Strength'))
    dict = collections.OrderedDict()
    dict['type'] = "directional"
    dict['direction'] = junction_path(directions)
    if emissionType == EmissionType.BLACKBODY:
        dict['temperature'] = junction_path(radiances)
    else:
        dict['radiance'] = junction_path(radiances)
    dict['scale'] = junction_path(scales)
    return dict

def write_background(self, outNodeInput):
    dict = collections.OrderedDict()
    if len(outNodeInput.links) == 0:
        raise Exception("There is no background; please check prior to writing background")
    
    dict['scale'] = 1.0
    colorNode = outNodeInput.links[0].from_node
    if colorNode.bl_idname == 'ShaderNodeBackground':
        if len(colorNode.inputs['Color'].links) == 0:
            # Plain monochromatic background
            dict['type'] = 'envmap'
            if len(colorNode.inputs['Strength'].links) > 0:
                self.report({'WARNING'}, ("Background: non-scalar (linked) strength input for background is ignored (node '%s')"%(material.name, colorNode.name)))
            else:
                strength = colorNode.inputs['Strength'].default_value
                dict['scale'] = [ strength * colorNode.inputs['Color'].default_value[0], strength * colorNode.inputs['Color'].default_value[0], strength * colorNode.inputs['Color'].default_value[0] ]
        else:
            # Two valid options: direct connection from env texture or a background node inbetween
            if len(colorNode.inputs['Strength'].links) > 0:
                self.report({'WARNING'}, ("Background: non-scalar (linked) strength input for background is ignored (node '%s')"%(material.name, colorNode.name)))
            else:
                dict['scale'] = colorNode.inputs['Strength'].default_value
            colorNode = colorNode.inputs['Color'].links[0].from_node
    
    # Check if we have a color input node
    if colorNode.bl_idname != 'ShaderNodeBackground':
        if colorNode.bl_idname == 'ShaderNodeTexEnvironment' or colorNode.bl_idname == 'ShaderNodeTexImage':
            dict['type'] = 'envmap'
            if len(colorNode.inputs['Vector'].links) > 0:
                self.report({'WARNING'}, ("Envmap: vector input (for e.g. rotation) is not supported yet (node '%s')"%(colorInNode.name)))
            # TODO: warn about ignored options?
            dict['map'] = make_path_relative_to_root(colorNode.image.filepath)
        elif colorNode.bl_idname == 'ShaderNodeTexSky':
            if len(colorNode.inputs['Vector'].links) > 0:
                self.report({'WARNING'}, ("Sky: vector input (for e.g. rotation) is not supported yet (node '%s')"%(colorInNode.name)))
            dict['type'] = 'sky'
            dict['model'] = 'hosek'
            dict['turbidity'] = colorNode.turbidity
            dict['albedo'] = colorNode.ground_albedo
            dict['sunDir'] = [ colorNode.sun_direction.x, colorNode.sun_direction.z, -colorNode.sun_direction.y ]
    return dict

class CustomJSONEncoder(json.JSONEncoder):
    # https://stackoverflow.com/questions/50700585/write-json-float-in-scientific-notation
    def iterencode(self, o, _one_shot=False, level=1):
        indent = ' ' * (level * self.indent)
        if isinstance(o, float):
            return format(o, '.3g') # keep 3 most significant digits
        elif isinstance(o, Mapping):
            return "{{{}\n{}}}".format(','.join('\n{}"{}" : {}'.format(indent, str(ok), self.iterencode(ov, _one_shot, level+1))
                                       for ok, ov in o.items()), ' ' * ((level-1) * self.indent))
        elif isinstance(o, Sequence) and not isinstance(o, str):
            # Do not line break short arrays
            sep = ', ' if len(o) <= 4 else (',\n    '+indent)
            return "[{}]".format(sep.join(map(self.iterencode, o)))
        return ',\n'.join(super().iterencode(o))



# Blender has: up = z, but target is: up = y
def flip_space(vec):
    return [vec[0], vec[2], -vec[1]]

rootFilePath = ""


def make_path_relative_to_root(blenderPath):
    absPath = bpy.path.abspath(blenderPath)
    finalPath = os.path.relpath(absPath, rootFilePath)
    finalPath = finalPath.replace("\\", "/")
    return finalPath

# Takes an array of anything and returns either the array or an array containing one element if they're all the same
def junction_path(path):
    if path[1:] == path[:-1]:
        return [path[0]]
    return path
    

# Overwrite a numeric value within a bytearray
def write_num(binary, offset, size, num):
    binary[offset : offset+size] = num.to_bytes(size, byteorder='little')



# If the object has LoDs there are two options:
# It is a LoD (mesh only) OR an instance of a LoD-chain.
def is_lod_mesh(obj):
    # By definition a lod instance may not have any real data (but must be of type mesh).
    # I.e. it has no geometry == no dimension.
    # TODO: LoD levels!
    return False
    #return len(obj.lod_levels) != 0 and (obj.dimensions[0] != 0.0 or obj.dimensions[1] != 0.0 or obj.dimensions[2] != 0.0)

# Check, if an object is an exportable instance.
def is_instance(obj):
    # Not used by any scene
    if obj.users == 0:
        return False
    # At the moment only meshes and perfect spheres can be exported.
    # Maybe there is an NURBS or Bezier-spline export in feature...
    # 'mufflon_sphere' is a custom property used to flag perfect spheres.
    # Camera, Lamp, ... are skipped by this if, too.
    if obj.type != "MESH" and not obj.mufflon_sphere:
        return False
    # Skip meshes which do not have any faces
    if obj.type == "MESH" and len(obj.data.polygons) == 0:
        return False
    # An object can be 'usual', 'lod_instance' xor 'lod_mesh' where the latter is not an instance.
    if is_lod_mesh(obj):
        return False
    return True



def write_vertex_normals(vertexDataArray, mesh, use_compression):
    if mesh.has_custom_normals:
        # Create a lookuptable vertex -> any loop
        loopNormals = [None] * len(mesh.vertices)
        for i,l in enumerate(mesh.loops):
            loopNormals[l.vertex_index] = l.normal
        # Write out the normals
        for k in range(len(mesh.vertices)):
            # Sometimes not all vertices have custom normals (I suspect due to left-over vertices no longer in any face)
            if loopNormals[k] is None:
                normal = mesh.vertices[k].normal
            else:
                normal = loopNormals[k]
            if use_compression:
                vertexDataArray.extend(pack_normal32(normal).to_bytes(4, byteorder='little', signed=False))
            else:
                vertexDataArray.extend(struct.pack('<3f', *normal))
    else:
        for k in range(len(mesh.vertices)):
            if use_compression:
                vertexDataArray.extend(pack_normal32(mesh.vertices[k].normal).to_bytes(4, byteorder='little', signed=False))
            else:
                vertexDataArray.extend(struct.pack('<3f', *mesh.vertices[k].normal))

# Check if the transformation is valid. In case of spheres there should not be a rotation or
# non-uniform scaling.
def validate_transformation(self, instance):
    if instance.mufflon_sphere:
        if instance.rotation_euler != mathutils.Euler((0.0, 0.0, 0.0), 'XYZ'):
            self.report({'WARNING'}, ("Perfect sphere object \"%s\" has a rotation which will be ignored." % (instance.name)))
        if instance.scale[0] != instance.scale[1] or instance.scale[0] != instance.scale[2]:
            self.report({'WARNING'}, ("Perfect sphere object \"%s\" has a non-uniform scaling which will be ignored (using x-scale as uniform scale)." % (instance.name)))
        return mathutils.Matrix.Translation(instance.location) @ mathutils.Matrix.Scale(instance.scale[0], 4)
    else:
        return instance.matrix_world
        
# Computes the UV coordinate of a vertex based on spherical projection
def spherical_projected_uv_coordinate(vertexCoords):
    theta = math.acos(vertexCoords[1]/((1e-20 + math.sqrt((vertexCoords[0]*vertexCoords[0]) + (vertexCoords[1]*vertexCoords[1]) + (vertexCoords[2]*vertexCoords[2])))))
    phi = numpy.arctan2(vertexCoords[2], vertexCoords[0])
    if theta < 0:
        theta += math.pi
    if(phi < 0):
        phi += 2*math.pi
    u = theta / math.pi
    v = phi / (2*math.pi)
    return [u, v]

def write_instance_transformation(binary, transformMat):
    # As of version 1.4 we store the inverted matrices (ie. world-to-instance instead of instance-to-world)
    invTransformMat = mathutils.Matrix([ transformMat[0], transformMat[2], -transformMat[1], transformMat[3] ])
    invTransformMat.invert()
    # Apply the flip_space transformation on instance transformation level.
    binary.extend(struct.pack('<4f', *invTransformMat[0]))
    binary.extend(struct.pack('<4f', *invTransformMat[1]))
    binary.extend(struct.pack('<4f', *invTransformMat[2]))

def is_animated_instance(instance):
    # Check if there is an animation block or constraints
    if (instance.constraints is not None) and (len(instance.constraints) > 0):
        return True
    if instance.animation_data is not None:
        # We also have to check uf if the instance is rigged, in which case we don't export keyframed instances
        for mod in instance.modifiers:
            if mod.type == 'ARMATURE':
                return False
        return True
    return False
    
def get_blender_viewport_near_far_planes(context):
    # Try to find a suitable area
    ctxArea = None
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                ctxArea = area
                break
        if ctxArea is not None:
            break
    # Fallback: use some random one
    if ctxArea is None:
        ctxArea = context.window_manager.windows[0].screen.areas[0]
    
    oldAreaType = ctxArea.type
    ctxArea.type = 'VIEW_3D'
    
    clipStart = ctxArea.spaces[0].clip_start
    clipEnd = ctxArea.spaces[0].clip_end

    ctxArea.type = oldAreaType
    return clipStart, clipEnd


def export_json(context, self, binfilepath):
    version = "1.6"
    binary = os.path.relpath(binfilepath, os.path.commonpath([self.filepath, binfilepath]))
    global rootFilePath; rootFilePath = os.path.dirname(self.filepath)

    scn = context.scene
    dataDictionary = collections.OrderedDict()

    if os.path.isfile(self.filepath):
        file = open(self.filepath, 'r')
        jsonStr = file.read()
        file.close()
        try:
            oldData = json.loads(jsonStr, object_pairs_hook=OrderedDict)  # loads the old json and preserves ordering
            dataDictionary = oldData.copy()
        except json.decoder.JSONDecodeError as e:
            self.report({'ERROR'}, "Old JSON has wrong format: " + str(e))
            return -1
        dataDictionary['version'] = version
        dataDictionary['binary'] = binary
        if self.overwrite_default_scenario or not ('defaultScenario' in oldData):
            dataDictionary['defaultScenario'] = context.scene.name
        else:
            dataDictionary['defaultScenario'] = oldData['defaultScenario']
        dataDictionary['cameras'] = oldData['cameras']
        dataDictionary['lights'] = oldData['lights']
        dataDictionary['materials'] = oldData['materials']
        dataDictionary['scenarios'] = oldData['scenarios']
    else:
        dataDictionary['version'] = version
        dataDictionary['binary'] = binary
        dataDictionary['defaultScenario'] = context.scene.name
        dataDictionary['cameras'] = collections.OrderedDict()
        dataDictionary['lights'] = collections.OrderedDict()
        dataDictionary['materials'] = collections.OrderedDict()
        dataDictionary['scenarios'] = collections.OrderedDict()

    # Store current frame to reset it later
    frame_current = scn.frame_current
    frame_range = range(scn.frame_start, scn.frame_end + 1) if self.export_animation else [frame_current]
    
    # Cameras
    cameras = [o for o in bpy.data.objects if o.type == 'CAMERA']
    
    # We export blender near/far planes
    clipNear, clipFar = get_blender_viewport_near_far_planes(context)

    for i in range(len(cameras)):
        cameraObject = cameras[i]
        camera = cameraObject.data
        if camera.users == 0:
            continue
        if camera.type == "PERSP":
            aperture = camera.dof.aperture_fstop if camera.dof.use_dof else 128.0
            if cameraObject.name not in dataDictionary['cameras']:
                dataDictionary['cameras'][cameraObject.name] = collections.OrderedDict()
            if aperture >= 128.0:
                cameraType = "pinhole"
                dataDictionary['cameras'][cameraObject.name]['type'] = cameraType
                # FOV might be horizontal or vertically, see https://blender.stackexchange.com/a/38571
                if scn.render.resolution_y > scn.render.resolution_x:
                    fov = camera.angle
                else:
                    # correct aspect ratio because camera.angle is defined in x direction
                    fov = math.atan(math.tan(camera.angle / 2) * scn.render.resolution_y / scn.render.resolution_x) * 2
                dataDictionary['cameras'][cameraObject.name]['fov'] = fov * 180 / 3.141592653589793  # convert rad to degree
            else:
                cameraType = "focus"
                dataDictionary['cameras'][cameraObject.name]['type'] = cameraType
                dataDictionary['cameras'][cameraObject.name]['focalLength'] = camera.lens
                dataDictionary['cameras'][cameraObject.name]['focusDistance'] = camera.dof.focus_distance
                dataDictionary['cameras'][cameraObject.name]['aperture'] = aperture
                # Getting the chip height is a bit more involved: since we may no longer explicitly set
                # the sensor size, we have to compute it from the vertical FoV, focal length, and 
                aspectRatio = scn.render.resolution_y / scn.render.resolution_x
                dataDictionary['cameras'][cameraObject.name]['chipHeight'] = math.tan(camera.angle / 2.0) * 2.0 * camera.lens * aspectRatio
            dataDictionary['cameras'][cameraObject.name]['near'] = clipNear
            dataDictionary['cameras'][cameraObject.name]['far'] = clipFar
        elif camera.type == "ORTHO":
            if cameraObject.name not in dataDictionary['cameras']:
                dataDictionary['cameras'][cameraObject.name] = collections.OrderedDict()
            cameraType = "ortho"
            dataDictionary['cameras'][cameraObject.name]['type'] = cameraType
            orthoWidth = camera.ortho_scale
            dataDictionary['cameras'][cameraObject.name]['width'] = orthoWidth
            orthoHeight = scn.render.resolution_y / scn.render.resolution_x * orthoWidth  # get aspect ratio via resolution
            dataDictionary['cameras'][cameraObject.name]['height'] = orthoHeight
        else:
            self.report({'WARNING'}, ("Skipping unsupported camera type: \"%s\" from: \"%s\"." % (camera.type, cameraObject.name)))
            continue
        cameraPath = []
        viewDirectionPath = []
        upPath = []
        for f in frame_range:
            scn.frame_set(f)
            trans, rot, scale = cameraObject.matrix_world.decompose()
            cameraPath.append(flip_space(cameraObject.location))
            viewDirectionPath.append(flip_space(rot @ Vector((0.0, 0.0, -1.0))))
            upPath.append(flip_space(rot @ Vector((0.0, 1.0, 0.0))))
        dataDictionary['cameras'][cameraObject.name]['path'] = junction_path(cameraPath)
        dataDictionary['cameras'][cameraObject.name]['viewDir'] = junction_path(viewDirectionPath)
        dataDictionary['cameras'][cameraObject.name]['up'] = junction_path(upPath)

    if len(dataDictionary['cameras']) == 0:
        self.report({'ERROR'}, "No camera found.")  # Stop if no camera was exported
        return -1

    # Lights
    lightNames = []

    lamps = [o for o in bpy.data.objects if o.type == 'LIGHT']
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
            if lampObject.name not in dataDictionary['lights']:
                dataDictionary['lights'][lampObject.name] = collections.OrderedDict()
            if lamp.use_nodes and lamp.node_tree:
                dataDictionary['lights'][lampObject.name].update(write_point_light(self, lamp, lampObject, emissionNode, scn, frame_range))
            else:
                # TODO: this needs nodes too for goniometric lights!
                positions = []
                intensities = []
                scales = []
                # Go through all frames, accumulate the to-be-exported quantities
                for f in frame_range:
                    scn.frame_set(f)
                    positions.append(flip_space(lampObject.location))
                    intensities.append([lamp.color.r, lamp.color.g, lamp.color.b])
                    scales.append(lamp.energy)
                dataDictionary['lights'][lampObject.name]['type'] = "point"
                dataDictionary['lights'][lampObject.name]['position'] = junction_path(positions)
                dataDictionary['lights'][lampObject.name]['intensity'] = junction_path(intensities)
                dataDictionary['lights'][lampObject.name]['scale'] = junction_path(scales)
        elif lamp.type == "SUN":
            if lampObject.name not in dataDictionary['lights']:
                dataDictionary['lights'][lampObject.name] = collections.OrderedDict()
            if lamp.use_nodes and lamp.node_tree:
                dataDictionary['lights'][lampObject.name].update(write_directional_light(self, lamp, lampObject, emissionNode, scn, frame_range))
            else:
                directions = []
                radiances = []
                scales = []
                # Go through all frames, accumulate the to-be-exported quantities
                for f in frame_range:
                    scn.frame_set(f)
                    directions.append(flip_space(lampObject.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))))
                    radiances.append([lamp.color.r, lamp.color.g, lamp.color.b])
                    scales.append(lamp.energy)
                dataDictionary['lights'][lampObject.name]['type'] = "directional"
                dataDictionary['lights'][lampObject.name]['direction'] = junction_path(directions)
                dataDictionary['lights'][lampObject.name]['radiance'] = junction_path(radiances)
                dataDictionary['lights'][lampObject.name]['scale'] = junction_path(scales)
        elif lamp.type == "SPOT":
            if lampObject.name not in dataDictionary['lights']:
                dataDictionary['lights'][lampObject.name] = collections.OrderedDict()
            if lamp.use_nodes and lamp.node_tree:
                dataDictionary['lights'][lampObject.name].update(write_spot_light(self, lamp, lampObject, emissionNode, scn, frame_range))
            else:
                positions = []
                directions = []
                intensities = []
                scales = []
                widths = []
                falloffs = []
                # Go through all frames, accumulate the to-be-exported quantities
                for f in frame_range:
                    scn.frame_set(f)
                    positions.append(flip_space(lampObject.location))
                    directions.append(flip_space(lampObject.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))))
                    intensities.append([lamp.color.r, lamp.color.g, lamp.color.b])
                    scales.append(lamp.energy)
                    widths.append(lamp.spot_size / 2)
                    # Try to match the inner circle for the falloff (not exact, blender seems buggy):
                    # https://blender.stackexchange.com/questions/39555/how-to-calculate-blend-based-on-spot-size-and-inner-cone-angle
                    falloffs.append(math.atan(math.tan(lamp.spot_size / 2) * math.sqrt(1-lamp.spot_blend)))
                dataDictionary['lights'][lampObject.name]['type'] = "spot"
                dataDictionary['lights'][lampObject.name]['position'] = junction_path(positions)
                dataDictionary['lights'][lampObject.name]['direction'] = junction_path(directions)
                dataDictionary['lights'][lampObject.name]['intensity'] = junction_path(intensities)
                dataDictionary['lights'][lampObject.name]['scale'] = junction_path(scales)
                dataDictionary['lights'][lampObject.name]['width'] = junction_path(widths)
                dataDictionary['lights'][lampObject.name]['falloffStart'] = junction_path(falloffs)
        else:
            self.report({'WARNING'}, ("Skipping unsupported lamp type: \"%s\" from: \"%s\"." % (lamp.type, lampObject.name)))
            continue
        lightNames.append(lampObject.name)
    scn.frame_set(frame_current)

    for scene in bpy.data.scenes:
        world = scene.world
        # TODO: Envmaps how in 2.8?
    
    # Make a dict with all instances, cameras, ...
    if self.use_selection:
        objects = [obj for obj in bpy.context.selected_objects if obj.users > 0]
    else:
        objects = [obj for obj in bpy.data.objects if obj.users > 0]

    # Materials
    objects = bpy.data.objects
    materialNames = set() # Names of used materials

    for obj in objects:
        if (obj.type == "MESH" or "sphere" in obj) and (obj.material_slots is not None):
            for materialSlot in obj.material_slots:
                if materialSlot.material is not None:
                    materialNames.add(materialSlot.material.name)

    materials = bpy.data.materials
    for i in range(len(materials)):
        material = materials[i]
        # if material is not used continue
        if material.name not in materialNames:
            continue
        if material.name not in dataDictionary['materials']:
            dataDictionary['materials'][material.name] = collections.OrderedDict()


        # First get the node that actually determines the material properties
        outputNode = find_material_output_node(material)
        if outputNode is None:
            print("Skipping material '%s' (no output node)..."%(material.name))
            continue
        # Then handle surface properties: check the connections backwards
        if len(outputNode.inputs['Surface'].links) == 0:
            print("Skipping material '%s' (no connection to surface output)..."%(material.name))
            continue
        firstNode = outputNode.inputs['Surface'].links[0].from_node
        try:
            if firstNode.bl_idname == 'ShaderNodeMixShader':
                workDictionary = write_mix_node(self, material, firstNode, False)
            elif firstNode.bl_idname == 'ShaderNodeBsdfGlass':
                workDictionary = write_glass_node(self, material, firstNode)
            else:
                workDictionary = write_nonrecursive_node(self, material, firstNode)
            if len(outputNode.inputs['Displacement'].links):
                displaceNode = outputNode.inputs['Displacement'].links[0].from_node
                if displaceNode.bl_idname == 'ShaderNodeDisplacement':
                    if len(displaceNode.inputs['Height'].links) > 0:
                        heightNode = displaceNode.inputs['Height'].links[0].from_node
                        if heightNode.bl_idname != 'ShaderNodeTexImage':
                            self.report({'Warning'}, ("Material '%s': displacement height input must be an image texture"%(material.name)))
                        elif len(displaceNode.inputs['Midlevel'].links) > 0:
                            self.report({'Warning'}, ("Material '%s': displacement midlevel input must be scalar"%(material.name)))
                        elif len(displaceNode.inputs['Scale'].links) > 0:
                            self.report({'Warning'}, ("Material '%s': displacement scale input must be scalar"%(material.name)))
                        else:
                            workDictionary['displacement'] = collections.OrderedDict()
                            workDictionary['displacement']['map'] = get_image_input(material, displaceNode, heightNode, "Height", True, self.bake_textures)
                            workDictionary['displacement']['bias'] = displaceNode.inputs['Midlevel'].default_value
                            workDictionary['displacement']['scale'] = displaceNode.inputs['Scale'].default_value
                    else:
                        self.report({'Warning'}, ("Material '%s': displacement height needs an image texture input"%(material.name)))   
                else:
                    self.report({'WARNING'}, ("Material '%s': displacement output is not recognized (must be Displacement node first)"%(material.name)))
            if len(outputNode.inputs['Volume'].links):
                self.report({'WARNING'}, ("Material '%s': volume output is not supported yet"%(material.name)))
            write_outer_medium(self, workDictionary, material)
            # Remove known keys from material if the type changed in between (keep unknown, because they
            # are probably user added content)
            remove_known_matkeys(dataDictionary['materials'][material.name])
            dataDictionary['materials'][material.name].update(workDictionary)
        except Exception as e:
            self.report({'ERROR'}, ("Material '%s' not converted: %s"%(material.name, str(e))))
            

    # Scenarios
    for scene in bpy.data.scenes:
        world = scene.world
        if scene.name not in dataDictionary['scenarios']:
            dataDictionary['scenarios'][scene.name] = collections.OrderedDict()
        cameraName = ''  # type: str
        if hasattr(scene.camera, 'name'):
            if scene.camera.type == 'CAMERA' and (scene.camera.data.type == "PERSP" or scene.camera.data.type == "ORTHO"):
                cameraName = scene.camera.name
            else:
                cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary
                # dataDictionary['cameras'] has at least 1 element otherwise the program would have exited earlier
        else:
            cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary

        dataDictionary['scenarios'][scene.name]['camera'] = cameraName
        dataDictionary['scenarios'][scene.name]['resolution'] = [scene.render.resolution_x, scene.render.resolution_y]

        sceneLightNames = []
        for sceneObject in scene.objects:
            if sceneObject.name in lightNames:
                sceneLightNames.append(sceneObject.name)
                    
        # Background
        # Check if the world (aka background) is set
        try:
            if world.use_nodes:
                worldOutNode = find_world_output_node(world.node_tree)
                if not worldOutNode is None:
                    if len(worldOutNode.inputs['Surface'].links) > 0:
                        backgroundName = scene.name + "_Background"
                        dataDictionary['lights'][backgroundName] = write_background(self, worldOutNode.inputs['Surface'])
                        lightNames.append(backgroundName)
                        sceneLightNames.append(backgroundName)
        except Exception as e:
            self.report({'ERROR'}, ("Background light did not get set: %s"%(str(e))))

        dataDictionary['scenarios'][scene.name]['lights'] = sceneLightNames
        dataDictionary['scenarios'][scene.name]['lod'] = 0
        # Material assignments
        # Always clear the assignments. If something is renamed it does not make sense to keep it.
        # The number/name of the binary materials is only the set which is exported.
        dataDictionary['scenarios'][scene.name]['materialAssignments'] = collections.OrderedDict()
        for material in materialNames:
            dataDictionary['scenarios'][scene.name]['materialAssignments'][material] = material

        # Object properties
        #if 'objectProperties' not in dataDictionary['scenarios'][scene.name]:
        #    dataDictionary['scenarios'][scene.name]['objectProperties'] = collections.OrderedDict()

        # Instance properties
        if 'instanceProperties' not in dataDictionary['scenarios'][scene.name]:
            dataDictionary['scenarios'][scene.name]['instanceProperties'] = collections.OrderedDict()

        # Mask instance objects which are not part of this scene (hidden/not part)
        omittedObjects = set(objects) - set(scene.objects)
        for obj in omittedObjects:
            if is_instance(obj):
                if obj.name not in dataDictionary['scenarios'][scene.name]['instanceProperties']:
                    dataDictionary['scenarios'][scene.name]['instanceProperties'][obj.name] = collections.OrderedDict()
                dataDictionary['scenarios'][scene.name]['instanceProperties'][obj.name]['mask'] = True

    # CustomJSONEncoder: Custom float formater (default values will be like 0.2799999994039535)
    # and which packs small arrays in one line
    dump = json.dumps(dataDictionary, indent=4, cls=CustomJSONEncoder)
    file = open(self.filepath, 'w')
    file.write(dump)
    file.close()
    return 0



#   ###   #  #   #     #     ###   #   #
#   #  #  #  ##  #    # #    #  #   # #
#   ###   #  # # #   #   #   ###     #
#   #  #  #  #  ##  #######  #  #    #
#   ###   #  #   #  #     #  #  #    #

def get_faces_to_triangulate(bm, triangulate):
    return [f for f in bm.faces if len(f.edges) > 4 or (triangulate and len(f.edges) > 3)]

def get_edges_to_split(bm):
    edgesToSplit = set([e for e in bm.edges if e.seam or not e.smooth])
    
    for f in bm.faces:
        if not f.smooth:
            for e in f.edges:
                edgesToSplit.add(e)
    return list(edgesToSplit)
    
def count_tri_quads(self, bm):
    numberOfTriangles = 0
    numberOfQuads = 0
    for face in bm.faces:
        numVertices = len(face.verts)
        if numVertices == 3:
            numberOfTriangles += 1
        elif numVertices == 4:
            numberOfQuads += 1
        else:
            self.report({'ERROR'}, ("%d Vertices in face from object: \"%s\"." % (numVertices, lod.name)))
            return 0, 0
    return numberOfTriangles, numberOfQuads

def prepare_object_mesh(self, depsgraph, lod):
    # Disabling the armature modifier so we get rest-pose vertex positions is done in export_binary
    mesh = lod.evaluated_get(depsgraph).data    # applies all modifiers
    bm = bmesh.new()
    bm.from_mesh(mesh)  # bmesh gives a local editable mesh copy
    facesToTriangulate = get_faces_to_triangulate(bm, self.triangulate)
    if len(facesToTriangulate) > 0:
        bmesh.ops.triangulate(bm, faces=facesToTriangulate, quad_method='BEAUTY', ngon_method='BEAUTY')
    # Split vertices if vertex has multiple uv coordinates (is used in multiple triangles)
    # or if it is not smooth
    
    if len(lod.data.uv_layers) > 0:
        # mark seams from uv islands
        if bpy.ops.uv.seams_from_islands.poll():
            bpy.ops.uv.seams_from_islands()
    edgesToSplit = get_edges_to_split(bm)
    if len(edgesToSplit) > 0:
        bmesh.ops.split_edges(bm, edges=edgesToSplit)

    numberOfTriangles, numberOfQuads = count_tri_quads(self, bm)
    # Only change the mesh if any changes have been performed
    if len(facesToTriangulate) > 0 or len(edgesToSplit) > 0:
        bm.to_mesh(mesh)
    bm.free()
    return mesh, numberOfTriangles, numberOfQuads

# Write some data block with (optional) deflation
# Valid to be called for empty data which will write nothing
def write_compressed(binary, data, use_deflation):
    if not data: return
    outData = data
    if use_deflation:
        outData = zlib.compress(data, 8)
        binary.extend(len(outData).to_bytes(4, byteorder='little')) # compressed size
        binary.extend(len(data).to_bytes(4, byteorder='little'))    # uncompressed size
    binary.extend(outData)

def write_string(binary, string):
    binary.extend(len(string.encode()).to_bytes(4, byteorder='little')) # Length (always wriite)
    if string:
        binary.extend(string.encode())

def write_attribute_header(binary, attrName, metaInfo, metaFlags, typeCode, byteSize):
    binary.extend("Attr".encode())
    write_string(binary, attrName)
    write_string(binary, metaInfo)
    binary.extend(metaFlags.to_bytes(4, byteorder='little'))
    binary.extend(typeCode.to_bytes(4, byteorder='little'))
    binary.extend(byteSize.to_bytes(8, byteorder='little'))

def write_mesh_lod(self, lodObject, objectFlags, objectFlagsBinaryPosition, mesh, binary, materialLookup, boneLookup):
    # Vertex data
    vertices = mesh.vertices
    mesh.calc_normals()
    if mesh.has_custom_normals:
        mesh.calc_normals_split()
    uvCoordinates = numpy.empty(len(vertices), dtype=object)
    if len(mesh.uv_layers) == 0:
        self.report({'WARNING'}, ("LOD Object: \"%s\" has no uv layers." % (lodObject.name)))
        for k in range(len(uvCoordinates)):
            uvCoordinates[k] = spherical_projected_uv_coordinate(vertices[k].co)
    else:
        uv_layer = mesh.uv_layers[0]
        for polygon in mesh.polygons:
            for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                uvCoordinates[mesh.loops[loop_index].vertex_index] = uv_layer.data[loop_index].uv
    
    vertexDataArray = bytearray()  # Used for deflation
    for k in range(len(vertices)):
        vertexDataArray.extend(struct.pack('<3f', *mesh.vertices[k].co))
    write_vertex_normals(vertexDataArray, mesh, self.use_compression)
    for k in range(len(vertices)):
        # Same issue as with normals: there may be vertices which are not part of any loop and thus don't have UV coordinates
        if uvCoordinates[k] is None:
            vertexDataArray.extend(struct.pack('<2f', 0.0, 0.0))
        else:
            vertexDataArray.extend(struct.pack('<2f', uvCoordinates[k][0], uvCoordinates[k][1]))
    write_compressed(binary, vertexDataArray, self.use_deflation)

    # Vertex Attributes
    numberOfVertexAttributes = 0
    for uvNumber in range(len(mesh.uv_layers)):  # get Number of Vertex Attributes
        if uvNumber == 0:
            continue
        uv_layer = mesh.uv_layers[uvNumber]
        if not uv_layer:
            continue
        numberOfVertexAttributes += 1
    for colorNumber in range(len(mesh.vertex_colors)):
        vertex_color = mesh.vertex_colors[colorNumber]
        if not vertex_color:
            continue
        numberOfVertexAttributes += 1
    if self.export_animation and lodObject.parent and lodObject.parent.type == 'ARMATURE':
        numberOfVertexAttributes += 1

    if numberOfVertexAttributes > 0:
        for uvNumber in range(1, len(mesh.uv_layers)):
            uv_layer = mesh.uv_layers[uvNumber]
            if not uv_layer:
                continue
            uvCoordinates = numpy.empty(len(vertices), dtype=object)
            vertexAttributeDataArray = bytearray()  # Used for deflation
            write_attribute_header(vertexAttributeDataArray, uv_layer.name, "AdditionalUV2D", 0, 16, len(vertices)*4*2)
            for polygon in mesh.polygons:
                for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                    uvCoordinates[mesh.loops[loop_index].vertex_index] = uv_layer.data[loop_index].uv
            for k in range(len(vertices)):
                vertexAttributeDataArray.extend(struct.pack('<2f', uvCoordinates[k][0], uvCoordinates[k][1]))
            write_compressed(binary, vertexAttributeDataArray, self.use_deflation)

        for colorNumber in range(len(mesh.vertex_colors)):
            vertex_color_layer = mesh.vertex_colors[colorNumber]
            if not vertex_color_layer:
                continue
            vertexColor = numpy.empty(len(vertices), dtype=object)
            vertexAttributeDataArray = bytearray()  # Used for deflation
            write_attribute_header(vertexAttributeDataArray, vertex_color_layer.name, "Color", 0, 17, len(vertices)*4*3)
            for polygon in mesh.polygons:
                for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                    vertexColor[mesh.loops[loop_index].vertex_index] = vertex_color_layer.data[loop_index].color
            for k in range(len(vertices)):
                vertexAttributeDataArray.extend(struct.pack('<3f', vertexColor[k][0], vertexColor[k][1], vertexColor[k][2]))
            write_compressed(binary, vertexAttributeDataArray, self.use_deflation)

        if self.export_animation and lodObject.parent and lodObject.parent.type == 'ARMATURE':
            # There is a bone animation, so we need the vertex weights
            vertexAttributeDataArray = bytearray()  # Used for deflation
            write_attribute_header(vertexAttributeDataArray, "AnimationWeights", "", 0, 19, len(vertices)*4*4)
            for vert in mesh.vertices:
                weights = [0, 0, 0, 0]  # Intially no weights
                idx = [0x003fffff, 0x003fffff, 0x003fffff, 0x003fffff]
                # Collect weights. If there are more than 4, keep only the 4 largest.
                for g in vert.groups:
                    w = g.weight
                    b = boneLookup[lodObject.parent.name + lodObject.vertex_groups[g.group].name]
                    # Insertion(sort) with overflow
                    for i in range(4):
                        if w > weights[i]:
                            w, weights[i] = weights[i], w
                            b, idx[i] = idx[i], b
                # Encode the weights
                for i in range(4):
                    if idx[i] > 0x003fffff:
                        self.report({'WARNING'}, ("LOD Object: \"%s\". A vertex references a bone index > 0x003fffff." % (lodObject.name)))
                    if weights[i] < 0 or weights[i] > 1:
                        self.report({'WARNING'}, ("LOD Object: \"%s\". A vertex weight is outside [0,1]." % (lodObject.name)))
                    code = (idx[i] & 0x003fffff) | (round(weights[i] * 1023) << 22)
                    vertexAttributeDataArray.extend(code.to_bytes(4, byteorder='little'))
            write_compressed(binary, vertexAttributeDataArray, self.use_deflation)

    # TODO more Vertex Attributes? (with deflation)

    # Triangles
    triangleDataArray = bytearray()  # Used for deflation
    for polygon in mesh.polygons:
        if len(polygon.vertices) == 3:
            for k in range(3):
                triangleDataArray.extend(polygon.vertices[k].to_bytes(4, byteorder='little'))
    triangleOutData = triangleDataArray
    if self.use_deflation and len(triangleDataArray) > 0:
        triangleOutData = zlib.compress(triangleDataArray, 8)
        binary.extend(len(triangleOutData).to_bytes(4, byteorder='little'))
        binary.extend(len(triangleDataArray).to_bytes(4, byteorder='little'))
    binary.extend(triangleOutData)
    # Quads
    quadDataArray = bytearray()  # Used for deflation
    for polygon in mesh.polygons:
        if len(polygon.vertices) == 4:
            for k in range(4):
                quadDataArray.extend(polygon.vertices[k].to_bytes(4, byteorder='little'))
    quadOutData = quadDataArray
    if self.use_deflation and len(quadDataArray) > 0:
        quadOutData = zlib.compress(quadDataArray, 8)
        binary.extend(len(quadOutData).to_bytes(4, byteorder='little'))
        binary.extend(len(quadDataArray).to_bytes(4, byteorder='little'))
    binary.extend(quadOutData)
    # Material IDs
    matIDDataArray = bytearray()
    if len(mesh.materials) == 0:
        self.report({'WARNING'}, ("LOD Object: \"%s\" has no materials." % (lodObject.name)))
    for polygon in mesh.polygons:
        if len(polygon.vertices) == 3:
            if len(mesh.materials) != 0:
                matIDDataArray.extend(materialLookup[mesh.materials[polygon.material_index].name].to_bytes(2, byteorder='little'))
            else:
                matIDDataArray.extend((0).to_bytes(2, byteorder='little'))
    for polygon in mesh.polygons:
        if len(polygon.vertices) == 4:
            if len(mesh.materials) != 0:
                matIDDataArray.extend(materialLookup[mesh.materials[polygon.material_index].name].to_bytes(2, byteorder='little'))
            else:
                matIDDataArray.extend((0).to_bytes(2, byteorder='little')) # first material is default when the object has no mats
    if (objectFlags & 1) == 0:
        if len(mesh.materials) != 0:
            for polygon in mesh.polygons:
                if len(mesh.materials) != 0:
                    # Check if the material emits light
                    for node in mesh.materials[polygon.material_index].node_tree.nodes:
                        if node.bl_idname == 'ShaderNodeEmission' and (len(node.inputs['Strength'].links) > 0 or (node.inputs['Strength'].default_value > 0.0)):
                            objectFlags |= 1
                            objectFlagsBin = objectFlags.to_bytes(4, byteorder='little')
                            for k in range(4):
                                binary[objectFlagsBinaryPosition + k] = objectFlagsBin[k]
                            break
    else:
        if materials[0] > 0.0:  # first material is default when the object has no mats
            objectFlags |= 1
            objectFlagsBin = objectFlags.to_bytes(4, byteorder='little')
            for k in range(4):
                binary[objectFlagsBinaryPosition + k] = objectFlagsBin[k]
    matIDOutData = matIDDataArray
    if self.use_deflation and len(matIDDataArray) > 0:
        matIDOutData = zlib.compress(matIDDataArray, 8)
        binary.extend(len(matIDOutData).to_bytes(4, byteorder='little'))
        binary.extend(len(matIDDataArray).to_bytes(4, byteorder='little'))
    binary.extend(matIDOutData)
    # Face Attributes
    # TODO Face Attributes (with deflation)
    lodObject.to_mesh_clear()
    
    return numberOfVertexAttributes, 0
    
def write_sphere_lod(self, binary, lodObject, objectFlags, objectFlagsBinaryPosition, boundingBoxMin, boundingBoxMax, materialLookup):
    # Spheres
    sphereDataArray = bytearray()
    center = ( (boundingBoxMin[0] + boundingBoxMax[0]) / 2,
               (boundingBoxMin[1] + boundingBoxMax[1]) / 2,
               (boundingBoxMin[2] + boundingBoxMax[2]) / 2 )
    radius = abs(boundingBoxMin[0]-center[0])
    sphereDataArray.extend(struct.pack('<3f', *center))
    sphereDataArray.extend(struct.pack('<f', radius))

    if lodObject.active_material is not None:
        sphereDataArray.extend(materialLookup[lodObject.active_material.name].to_bytes(2, byteorder='little'))
        if (objectFlags & 1) == 0:
            if 'Emission' in lodObject.active_material.node_tree.nodes:
                objectFlags |= 1
                write_num(binary, objectFlagsBinaryPosition, 4, objectFlags)
    else:
        self.report({'WARNING'}, ("LOD Object: \"%s\" has no materials." % (lodObject.name)))
        sphereDataArray.extend((0).to_bytes(2, byteorder='little'))  # first material is default when the object has no mats

    sphereOutData = sphereDataArray
    # TODO Sphere Attributes
    if self.use_deflation and len(sphereDataArray) > 0:
        sphereOutData = zlib.compress(sphereDataArray, 8)
        binary.extend(len(sphereOutData).to_bytes(4, byteorder='little'))
        binary.extend(len(sphereDataArray).to_bytes(4, byteorder='little'))
    binary.extend(sphereOutData)
    return 0
    
def write_object_aabb_and_detect_lods(binary, currentObject, currObjectName, keyframe):
    # Bounding box
    # Calculate Lod chain to get bounding box over all Lods
    lodLevels = []
    lodChainStart = 0
    # TODO: LoD chain
    if len(lodLevels) == 0:
        lodLevels.append(currentObject)  # if no LOD levels the object itself is the only LOD level

    boundingBox = lodLevels[0].bound_box

    boundingBoxMin = [boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]]
    boundingBoxMax = [boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]]

    for lodObject in lodLevels:
        boundingBox = lodObject.bound_box
        # Set bounding box min/max to last element of the bb
        # boundingBoxMin = flip_space((boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]))
        # boundingBoxMax = boundingBoxMin.copy()

        for j in range(7):  # 0-6
            corner = [ boundingBox[j][0], boundingBox[j][1], boundingBox[j][2] ]
            for k in range(3):  # x y z
                if corner[k] < boundingBoxMin[k]:
                    boundingBoxMin[k] = corner[k]
                if corner[k] > boundingBoxMax[k]:
                    boundingBoxMax[k] = corner[k]
    binary.extend(struct.pack('<3f', *boundingBoxMin))    # '<' = little endian  3 = 3 times f  'f' = float32
    binary.extend(struct.pack('<3f', *boundingBoxMax))
    return lodLevels, lodChainStart, boundingBoxMin, boundingBoxMax

def write_object_binary(self, context, depsgraph, binary, materialLookup, boneLookup, currentObject, currObjectName, keyframe):
    # First write object header information
    binary.extend("Obj_".encode())                                  # Type check
    objectName = currObjectName.encode()                            # Object name
    objectNameLength = len(objectName)
    binary.extend(objectNameLength.to_bytes(4, byteorder='little'))
    binary.extend(objectName)
    # Write the object flags (NOT compression/deflation, but rather isEmissive etc)
    objectFlagsBinaryPosition = len(binary)
    objectFlags = 0
    binary.extend(objectFlags.to_bytes(4, byteorder='little'))
    binary.extend(keyframe.to_bytes(4, byteorder='little'))         # Keyframe
    # OBJID of previous object in animation
    binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))     # TODO keyframes

    lodLevels, lodChainStart, boundingBoxMin, boundingBoxMax = write_object_aabb_and_detect_lods(binary, currentObject, currObjectName, keyframe)
    
    # <Jump Table> LOD
    # Number of entries in table
    binary.extend((len(lodLevels)).to_bytes(4, byteorder='little'))
    lodStartBinaryPosition = len(binary)
    # Jump table for LoDs
    for j in range(len(lodLevels)):
        binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known
    # Write the actual LoD data (vertices, normals etc.)
    for j in range(len(lodLevels)):
        lodObject = lodLevels[(lodChainStart+j+1) % len(lodLevels)]  # for the correct starting object
        # start Positions
        write_num(binary, lodStartBinaryPosition + j*8, 8, len(binary))
        # Type
        binary.extend("LOD_".encode())
        # Needs to set the target object to active, to be able to apply changes.
        objScenes = lodObject.users_scene
        if len(objScenes) < 1:
            continue
        context.window.scene = objScenes[0] # Choose a valid scene which contains the object
        hidden = lodObject.hide_render
        lodObject.hide_render = False
        context.view_layer.objects.active = lodObject
        if not lodObject.mufflon_sphere:
            mesh, numberOfTriangles, numberOfQuads = prepare_object_mesh(self, depsgraph, lodObject)
            binary.extend(numberOfTriangles.to_bytes(4, byteorder='little'))
            binary.extend(numberOfQuads.to_bytes(4, byteorder='little'))
            binary.extend((0).to_bytes(4, byteorder='little'))                  # Num. spheres
            binary.extend(len(mesh.vertices).to_bytes(4, byteorder='little'))
            binary.extend(len(mesh.edges).to_bytes(4, byteorder='little'))
            numVertAttrBinaryPosition = len(binary)  # has to be corrected when value is known
            binary.extend((0).to_bytes(4, byteorder='little'))
            numFaceAttrBinaryPosition = len(binary)  # has to be corrected when value is known
            binary.extend((0).to_bytes(4, byteorder='little'))
            binary.extend((0).to_bytes(4, byteorder='little'))
            numVertAttr, numFaceAttr = write_mesh_lod(self, lodObject, objectFlags, objectFlagsBinaryPosition, mesh, binary, materialLookup, boneLookup)
            write_num(binary, numVertAttrBinaryPosition, 4, numVertAttr)
            write_num(binary, numFaceAttrBinaryPosition, 4, numFaceAttr)
        else:
            binary.extend((0).to_bytes(4, byteorder='little'))
            binary.extend((0).to_bytes(4, byteorder='little'))
            binary.extend((1).to_bytes(4, byteorder='little'))
            binary.extend((0).to_bytes(4, byteorder='little'))
            binary.extend((0).to_bytes(4, byteorder='little'))
            binary.extend((0).to_bytes(4, byteorder='little'))
            binary.extend((0).to_bytes(4, byteorder='little'))
            numSphereAttrBinaryPosition = len(binary)  # has to be corrected when value is known
            binary.extend((0).to_bytes(4, byteorder='little'))
            numSphereAttr = write_sphere_lod(self, binary, lodObject, objectFlags, objectFlagsBinaryPosition, boundingBoxMin, boundingBoxMax, materialLookup)
            write_num(binary, numSphereAttrBinaryPosition, 4, numSphereAttr)
    # reset used state
    lodObject.hide_render = hidden


def write_animation_binary(self, context, binary, frame_range):
    binary.extend("Bone".encode())
    animSectionOffsetPos = len(binary)
    binary.extend((0).to_bytes(8, byteorder='little'))
    # Get all armature objects
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    # Early-out if there are no armatures
    if not armatures or not self.export_animation:
        binary.extend((0).to_bytes(4, byteorder='little'))
        binary.extend((0).to_bytes(4, byteorder='little'))
        write_num(binary, animSectionOffsetPos, 8, len(binary))
        return dict()
    
    print("Exporting skeleton animation data...")
    # Create index -> bone mapping
    boneLookup = dict()
    count = 0
    for arm in armatures:
        for bone in arm.pose.bones:
            fullName = arm.name + bone.name
            boneLookup[fullName] = count
            count = count + 1
    binary.extend(count.to_bytes(4, byteorder='little'))
    nkeys = len(frame_range)
    binary.extend(nkeys.to_bytes(4, byteorder='little'))

    oldFrame = context.scene.frame_current
    # Export all matrices for all keyframes
    for frame in frame_range:
        if frame != context.scene.frame_current:
            context.scene.frame_set(frame)
        for arm in armatures:
            for bone in arm.pose.bones:
                # For the transformation matrix see http://rodolphe-vaillant.fr/?e=77
                worldRestMat = arm.convert_space(pose_bone=bone, matrix=bone.bone.matrix_local,
                                                 from_space='POSE', to_space='WORLD')
                worldPoseMat = arm.convert_space(pose_bone=bone, matrix=bone.matrix,
                                                 from_space='POSE', to_space='WORLD')
                transMat = worldPoseMat @ worldRestMat.inverted()
                # Convert into dual quaternion
                translation, q0, scale = transMat.decompose()
                qe = mathutils.Quaternion((0.0, translation.x / 2.0, translation.y / 2.0, translation.z / 2.0))
                qe = qe @ q0
                # Swap the quaternion element order, since we expect i, j, k, r instead of w, x, y, z
                q0 = mathutils.Quaternion((q0.x, q0.y, q0.z, q0.w))
                qe = mathutils.Quaternion((qe.x, qe.y, qe.z, qe.w))
                binary.extend(struct.pack('<4f', *q0))
                binary.extend(struct.pack('<4f', *qe))

    write_num(binary, animSectionOffsetPos, 8, len(binary))
    if oldFrame != context.scene.frame_current:
        context.scene.frame_set(oldFrame)
    return boneLookup

def get_bone_custom_shapes():
    boneCustomShapes = []
    for armature in [ob for ob in bpy.data.objects if ob.type == 'ARMATURE']:
        for bone in armature.pose.bones:
            if bone.custom_shape is not None:
                boneCustomShapes.append(bone.custom_shape)
    return boneCustomShapes

def write_instances(self, binary, instances, exportedObjects):
    # Type
    binary.extend("Inst".encode())
    # Number of Instances
    numberOfInstancesBinaryPosition = len(binary)
    binary.extend((0).to_bytes(4, byteorder='little'))  # has to be corrected later
    numberOfInstances = 0
    print("Exporting all-frame instances...")
    perFrameInstances = []
    for currentInstance in instances:
        index = exportedObjects[currentInstance.data]
        # Check if the object has animation data
        if is_animated_instance(currentInstance):
            perFrameInstances.append(currentInstance)
            continue
        binary.extend(len(currentInstance.name.encode()).to_bytes(4, byteorder='little'))
        binary.extend(currentInstance.name.encode())
        binary.extend(index.to_bytes(4, byteorder='little'))  # Object ID
        binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little')) # Keyframe
        binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Instance ID
        write_instance_transformation(binary, validate_transformation(self, currentInstance))
        numberOfInstances += 1
        
    print("Exporting per-frame instances...")
    if len(perFrameInstances) > 0:
        for f in frame_range:
            scn.frame_set(f)
            # First the "normal" instances for this frame
            for currentInstance in perFrameInstances:
                transformMat = validate_transformation(self, currentInstance)
                index = exportedObjects[currentInstance.data]
                binary.extend(len(currentInstance.name.encode()).to_bytes(4, byteorder='little'))
                binary.extend(currentInstance.name.encode())
                binary.extend(index.to_bytes(4, byteorder='little'))  # Object ID
                binary.extend(f.to_bytes(4, byteorder='little')) # Keyframe
                binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Instance ID
                write_instance_transformation(binary, validate_transformation(self, currentInstance))
                numberOfInstances += 1
            # Then come the animated object's instances
            # Each object is exported for the entire frame range
            for i in range(0, len(animationObjects)):
                animatedInstance = animationObjects[i]
                # Implicit object index: there is no "real" instancing
                index = len(exportedObjects) + i * len(frame_range) + f - frame_range[0]
                name = (animatedInstance.name + "__animated__frame_" + str(f)).encode()
                binary.extend(len(name).to_bytes(4, byteorder='little'))
                binary.extend(name)
                binary.extend(index.to_bytes(4, byteorder='little'))  # Object ID
                binary.extend(f.to_bytes(4, byteorder='little')) # Keyframe
                binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Instance ID
                transformMat = validate_transformation(self, animatedInstance)
                write_instance_transformation(binary, transformMat)
                numberOfInstances += 1
    
    # Now that we're done we know the amount of instances
    numberOfInstancesBytes = numberOfInstances.to_bytes(4, byteorder='little')
    for i in range(4):
        binary[numberOfInstancesBinaryPosition + i] = numberOfInstancesBytes[i]

def export_binary(context, self, filepath):
    scn = context.scene
    # Store current frame to reset it later
    frame_current = scn.frame_current
    frame_range = range(scn.frame_start, scn.frame_end + 1) if self.export_animation else [frame_current]
    
    # Binary
    binary = bytearray()
    # Materials Header
    binary.extend("Mats".encode())
    materials = []
    materialNames = []
    materialNameLengths = []

    materialNamesDict = dict()

    for obj in bpy.data.objects:
        if obj.users != 0:
            if obj.type == "MESH" or "sphere" in obj:
                if obj.material_slots is not None:
                    for materialSlot in obj.material_slots:
                        if materialSlot.material is not None:
                            if materialSlot.material.name not in materialNamesDict:
                                materialNamesDict[materialSlot.material.name] = True
                                materials.append(materialSlot.material)

    materialLookup = collections.OrderedDict()  # Lookup for the polygons
    bytePosition = 16
    if len(materials) == 0:
        self.report({'ERROR'}, ("There are no materials in the scene."))
        return -1
    for i in range(len(materials)):
        material = materials[i]
        materialLookup[material.name] = i
        materialNames.append(material.name.encode())
        materialNameLength = len(material.name.encode())
        materialNameLengths.append(materialNameLength.to_bytes(4, byteorder='little'))
        bytePosition += 4 + materialNameLength

    binary.extend(bytePosition.to_bytes(8, byteorder='little'))
    binary.extend(len(materials).to_bytes(4, byteorder='little'))
    for i in range(len(materialNameLengths)):
        binary.extend(materialNameLengths[i])
        binary.extend(materialNames[i])

    # Write skeletal animation data
    boneLookup = write_animation_binary(self, context, binary, frame_range)

    # Objects Header

    # Type
    binary.extend("Objs".encode())
    instanceSectionStartBinaryPosition = len(binary)  # Save Position in binary has to be corrected later
    # Next section start position
    binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known
    # Global object flags (compression etc.)
    flags = 0x0
    if self.use_deflation:
        flags |= 1
    if self.use_compression:
        flags |= 2
    binary.extend(flags.to_bytes(4, byteorder='little'))

    # Get all real geometric objects for export and make them unique (instancing may refer to the same
    # mesh multiple times).
    if self.use_selection:
        instances = [obj for obj in bpy.context.selected_objects if is_instance(obj)]
    else:
        instances = [obj for obj in bpy.data.objects if is_instance(obj)]
    
    # If we have armatures its bones may have custom object representations which should not be exported
    instances = list(set(instances) - set(get_bone_custom_shapes()))
    
    animationObjects = []
    if self.export_animation:
        print("Checking for animated meshes...")
        # List of instances to remove because they're animated
        remainingInstances = []
        # To export deforming animations (such as fluid animations), we check if one of the modifiers for them is present
        for idx in range(0, len(instances)):
            instance = instances[idx]
            if instance.type == "MESH":
                fluidMods = [mod for mod in instance.modifiers if mod.type == "FLUID_SIMULATION"]
                clothMods = [mod for mod in instance.modifiers if mod.type == "CLOTH"]
                if fluidMods:
                    # Check if we're the fluid, in which case we simply don't export the object
                    if fluidMods[0].settings.type == "DOMAIN":
                        animationObjects.append(instance)
                    elif fluidMods[0].settings.type != "FLUID":
                        remainingInstances.append(instance)
                elif clothMods:
                    animationObjects.append(instance)
                else:
                    remainingInstances.append(instance)
                
        instances = remainingInstances
    
    # Get the number of unique data references. While it hurts to perform an entire set construction
    # there is no much better way. The object count must be known to construct the jump-table properly.
    countOfObjects = len({obj.data for obj in instances}) + len(animationObjects) * len(frame_range)
    binary.extend(countOfObjects.to_bytes(4, byteorder='little'))

    objectStartBinaryPosition = []  # Save Position in binary to set this correct later
    for i in range(countOfObjects):
        objectStartBinaryPosition.append(len(binary))
        binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known

    print("Exporting objects...")
    activeObject = context.view_layer.objects.active    # Keep this for resetting later
    exportedObjects = OrderedDict()
    mode = 'OBJECT'
    if context.object:
        mode = context.object.mode   # Keep this for resetting later
        bpy.ops.object.mode_set(mode='OBJECT')
    
    # Evaluating the dependency graph can be an expensive operation - it's best to evaluate it once!
    depsgraph = context.evaluated_depsgraph_get()
    # If we're exporting a rigged mesh, we have to temporarily disable rigging since
    # we expects the vertices in rest position
    armMods = []
    if self.export_animation:
        for obj in instances:
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE' and mod.show_viewport:
                    armMods.append(mod)
                    mod.show_viewport = False
        depsgraph.update()
    
    # Export regular objects
    print("Exporting non-animated objects...")
    for currentObject in instances:
        # Due to instancing a mesh might be referenced multiple times
        if currentObject.data in exportedObjects:
            continue
        print(currentObject.name)
        idx = len(exportedObjects)
        exportedObjects[currentObject.data] = idx # Store index for the instance export
        write_num(binary, objectStartBinaryPosition[idx], 8, len(binary)) # object start position
        write_object_binary(self, context, depsgraph, binary, materialLookup, boneLookup,
                            currentObject, currentObject.data.name, 0xFFFFFFFF)
    
    # Export animated objects (cloth, fluid etc.)
    # TODO: shape key support?
    print("Exporting animated objects...")
    idx = len(exportedObjects)
    for currentObject in animationObjects:
        print(currentObject.name)
        # These need to be exported for every frame
        for f in frame_range:
            scn.frame_set(f)
            # Implicit object index (no instancing supported)
            write_num(binary, objectStartBinaryPosition[idx], 8, len(binary)) # object start position
            write_object_binary(self, context, depsgraph, binary, materialLookup, boneLookup, currentObject,
                                currentObject.data.name + "__animated__frame_" + str(f), f)
            idx += 1
    
    # Reset the armature modifier visibilities
    if self.export_animation:
        for mod in armMods:
            mod.show_viewport = True
        depsgraph.update()
    
    #reset active object
    context.view_layer.objects.active = activeObject
    if mode != 'OBJECT':
        bpy.ops.object.mode_set(mode=mode)

    # Export instances
    write_num(binary, instanceSectionStartBinaryPosition, 8, len(binary))
    write_instances(self, binary, instances, exportedObjects)

    # Reset scene
    if frame_current != scn.frame_current:
        scn.frame_set(frame_current)
    context.window.scene = scn
    # Write binary to file
    binFile = open(filepath, 'bw')
    binFile.write(binary)
    binFile.close()
    return 0


def export_mufflon(context, self):
    filename = os.path.splitext(self.filepath)[0]
    binfilepath = filename + ".mff"
    if export_json(context, self, binfilepath) == 0:
        print("Succeeded exporting JSON")
        if export_binary(context, self, binfilepath) == 0:
            print("Succeeded exporting binary")
        else:
            print("Failed exporting binary")
            print("Stopped exporting")
            return {'CANCELLED'}
    else:
        print("Failed exporting JSON")
        print("Stopped exporting")
        return {'CANCELLED'}
    return {'FINISHED'}

def pack_normal32(vec3):
    l1norm = abs(vec3[0]) + abs(vec3[1]) + abs(vec3[2])
    if l1norm == 0: # Prevent division by zero
        l1norm = 1e-7
    if vec3[2] >= 0:
        u = vec3[0] / l1norm
        v = vec3[1] / l1norm
    else:  # warp lower hemisphere
        u = (1 - abs(vec3[1]) / l1norm) * (1 if vec3[0] >= 0 else -1)
        v = (1 - abs(vec3[0]) / l1norm) * (1 if vec3[1] >= 0 else -1)
    u = math.floor(u * 32767.0 + 0.5)  # from [-1,1] to [-2^15,2^15-1]
    v = math.floor(v * 32767.0 + 0.5)  # from [-1,1] to [-2^15,2^15-1]
    return ctypes.c_ushort(u).value | ctypes.c_uint(v << 16).value

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import (ExportHelper, path_reference_mode)
from bpy.props import StringProperty, BoolProperty, EnumProperty, PointerProperty, FloatProperty, FloatVectorProperty
from bpy.types import Operator, Panel, PropertyGroup


class MufflonExporter(Operator, ExportHelper):
    """This appears in the tooltip of the operator and in the generated docs"""
    bl_idname = "mufflon.exporter"  # important since its how bpy.ops.import_test.some_data is constructed
    bl_label = "Export Mufflon Scene"

    # ExportHelper mixin class uses this
    filename_ext = ".json"
    filter_glob: StringProperty(
            default="*.json;*.mff",
            options={'HIDDEN'},
            maxlen=255,  # Max internal buffer length, longer would be clamped.
            )

    # List of operator properties, the attributes will be assigned
    # to the class instance from the operator settings before calling.
    use_selection: BoolProperty(
            name="Selection Only",
            description="Export selected objects only",
            default=False,
            )
    use_compression: BoolProperty(
            name="Compress normals",
            description="Apply compression to vertex normals (Octahedral)",
            default=False,
            )
    use_deflation: BoolProperty(
            name="Deflate",
            description="Use deflation to reduce file size",
            default=False,
            )
    triangulate: BoolProperty(
            name="Triangulate",
            description="Triangulates all exported objects",
            default=False,
            )
    overwrite_default_scenario: BoolProperty(
            name="Overwrite default scenario",
            description="Overwrite the default scenario when exporting JSON if already set",
            default=True,
            )
    export_animation: BoolProperty(
            name="Export animation",
            description="Exports instance transformations per animation frame",
            default=False,
            )
    bake_textures: BoolProperty(
            name="Bake procedural textures",
            description="Bakes procedural textures used as e.g. color inputs and stores them on disk",
            default=False
            )
    path_mode = path_reference_mode

    def execute(self, context):
        return export_mufflon(context, self)


# Only needed if you want to add into a dynamic menu
def menu_func_export(self, context):
    self.layout.operator(MufflonExporter.bl_idname, text="Mufflon (.json/.mff)")

class OuterMediumProperties(PropertyGroup):
    enabled: BoolProperty(
        name = "Enable outer medium",
        description = "If disabled the outside of an object is assumed to be vacuum. The outer medium allows a different specification.",
        default = False
    )
    ior: FloatProperty(
        name = "IOR",
        description = "Index of Refraction",
        min = 0.0,
        default = 1.0
    )
    transmission: FloatVectorProperty(
        name = "Transmission",
        description = "Gets mapped to an absorption equal to that of BSDF color parameters.",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
    )

class OuterMediumPanel(Panel):
    bl_idname = "MATERIAL_PT_outer_medium"
    bl_label = "Outer Medium"
    bl_space_type = "PROPERTIES"
    bl_region_type = 'WINDOW'
    bl_context = "material"

    def draw(self, context):
        self.layout.use_property_split = True
        self.layout.enabled = context.active_object.active_material.outer_medium.enabled
        self.layout.prop(context.active_object.active_material.outer_medium, "ior")
        self.layout.prop(context.active_object.active_material.outer_medium, "transmission")

    def draw_header(self, context):
        self.layout.prop(context.active_object.active_material.outer_medium, "enabled", text="")

class SpherePanel(Panel):
    bl_idname = "OBJECT_PT_mufflon_sphere"
    bl_label = "Perfect Sphere (Mufflon)"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    def draw(self, context):
        pass

    def draw_header(self, context):
        self.layout.prop(context.active_object, "mufflon_sphere", text="")


classes = (
    MufflonExporter,
    OuterMediumProperties,
    OuterMediumPanel,
    SpherePanel
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

    bpy.types.Material.outer_medium = PointerProperty(type=OuterMediumProperties)
    bpy.types.Object.mufflon_sphere = BoolProperty()


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)


if __name__ == "__main__":
    unregister()
