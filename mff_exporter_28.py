
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
from inspect import currentframe, getframeinfo
from collections.abc import Mapping, Sequence

bl_info = {
    "name": "Mufflon Exporter",
    "description": "Exporter for the custom Mufflon file format",
    "author": "Marvin, Johannes Jendersie, Florian Bethe",
    "version": (1, 1),
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


def find_material_output_node(tree):
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputMaterial' and node.is_active_output:
            return node
    return None
    
def find_world_output_node(tree):
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputWorld' and node.is_active_output:
            return node
    return None
    

def bake_texture_node(node, material, outputName, isScalar):
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
    outputNode = find_material_output_node(material.node_tree)
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
    fileName = material.name + "_" + node.name + ".png"
    filePath = "//baked_textures//" + fileName
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

def get_image_input(material, to_node, from_node, targetInputName, isScalar):
    if from_node.bl_idname == 'ShaderNodeTexImage':
        return from_node.image.filepath
    else:
        # Find out what socket we take things from
        fromSocketName = to_node.inputs[targetInputName].links[0].from_socket.name
        return bake_texture_node(from_node, material, fromSocketName, isScalar)

def get_color_input(material, node, inputName):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return property_array_to_color(input.default_value)
    else:
        return get_image_input(material, node, input.links[0].from_node, inputName, False)
        
def get_scalar_input(material, node, inputName):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return input.default_value
    else:
        # TODO: support for converters!
        return get_image_input(material, node, input.links[0].from_node, inputName, True)

def get_scalar_def_only_input(node, inputName):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return input.default_value
    else:
        raise Exception("non-value where only value is expected (node '%s.%s')"%(node.name, inputName))
        
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
    dict['radiance'] = get_color_input(material, node, 'Color')
    scale = get_scalar_def_only_input(node, 'Strength')
    dict['scale'] = [ scale, scale, scale ]
    return dict
    
def write_diffuse_node(self, material, node):
    dict = collections.OrderedDict()

    if len(node.inputs['Roughness'].links) == 0:
        # Differentiate between Lambert and Oren-Nayar
        dict['albedo'] = get_color_input(material, node, 'Color')
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
    dict['albedo'] = get_color_input(material, node, 'Color')
    if node.distribution == 'SHARP':
        dict['roughness'] = 0.0
    else:
        dict['roughness'] = get_scalar_input(material, node, 'Roughness')
    dict['ndf'] = get_microfacet_distribution(self, material.name, node)
    # TODO: shadowing model
    dict['shadowingModel'] = 'vcavity'
    
    # Check for anisotropy
    if node.bl_idname == 'ShaderNodeBsdfAnisotropic':
        # TODO: rotation of anisotropy!
        # TODO: what to do about normal/tangent?
        dict['roughness'] = [ dict['roughness'], get_scalar_def_only_input(node, 'Anisotropy') ]
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
    dict['absorption'] = get_color_input(material, node, 'Color')
    # Check if the 'color' is a string or none (which means no inverting possible)
    # TODO: write 'color' out instead of absorption (since inverting a texture is difficult/expensive)
    if (dict['absorption'] is not None) and not isinstance(dict['absorption'], str):
        dict['absorption'][0] = 1.0 - dict['absorption'][0]
        dict['absorption'][1] = 1.0 - dict['absorption'][1]
        dict['absorption'][2] = 1.0 - dict['absorption'][2]
    if node.distribution == 'SHARP':
        dict['roughness'] = 0.0
    else:
        dict['roughness'] = get_scalar_input(material, node, 'Roughness')
    # TODO: shadowing model
    dict['shadowingModel'] = 'vcavity'
    dict['ndf'] = get_microfacet_distribution(self, material.name, node)
    dict['ior'] = get_scalar_def_only_input(node, 'IOR')
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
        dict["alpha"] = get_image_input(material, node, node.inputs['Fac'].links[0].from_node, 'Fac', True)
    else:
        raise Exception("invalid mix-shader factor input (node '%s')"%(node.name))
        
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

materialKeys = ["alpha", "displacement", "type", "albedo", "roughness", "ndf", "absorption", "radiance", "scale", "refractionIndex", "factorA", "factorB", "layerA", "layerB", "layerReflection", "layerRefraction"]
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
    # 'sphere' is a custom property used to flag perfect spheres.
    # Camera, Lamp, ... are skipped by this if, too.
    if obj.type != "MESH" and "sphere" not in obj:
        return False
    # An object can be 'usual', 'lod_instance' xor 'lod_mesh' where the latter is not an instance.
    if is_lod_mesh(obj):
        return False
    return True



def write_vertex_normals(vertexDataArray, mesh, use_compression):
    if mesh.has_custom_normals:
        # Create a lookuptable vertex -> any loop
        vertToLoop = [0] * len(mesh.vertices)
        for i,l in enumerate(mesh.loops):
            vertToLoop[l.vertex_index] = l
        # Write out the normals
        for k in range(len(mesh.vertices)):
            normal = vertToLoop[k].normal
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

# Creates and appends an object after applying all modifiers for each frame in range
def create_per_frame_object(scn, instance, animationObjects, frame_range, nameSnippet, smoothNormals=True):
    print("Converting '", instance.name, "' to per-frame mesh")
    # Now for each frame, add an object with applied modifiers
    for f in frame_range:
        scn.frame_set(f)
        mesh = instance.to_mesh(scn, True, calc_tessface=False, settings='RENDER')  # applies all modifiers
        # Animated objects (fluids, cloth) should have smooth surfaces -> smooth normals
        if smoothNormals:
            for p in mesh.polygons:
                p.use_smooth = True
        obj = bpy.data.objects.new(instance.name + nameSnippet + str(f), mesh)
        obj.matrix_world = instance.matrix_world
        scn.objects.link(obj)
        obj.select = True
        animationObjects.append(obj)

# Check if the transformation is valid. In case of spheres there should not be a rotation or
# non-uniform scaling.
def validate_transformation(self, instance):
    if "sphere" in instance:
        if instance.rotation_euler != mathutils.Euler((0.0, 0.0, 0.0), 'XYZ'):
            self.report({'WARNING'}, ("Perfect sphere object \"%s\" has a rotation which will be ignored." % (instance.name)))
        if instance.scale[0] != instance.scale[1] or instance.scale[0] != instance.scale[2]:
            self.report({'WARNING'}, ("Perfect sphere object \"%s\" has a non-uniform scaling which will be ignored (using x-scale as uniform scale)." % (instance.name)))
        return mathutils.Matrix.Translation(instance.location) * mathutils.Matrix.Scale(instance.scale[0], 4)
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
    # Apply the flip_space transformation on instance transformation level.
    binary.extend(struct.pack('<4f', *transformMat[0]))
    binary.extend(struct.pack('<4f', *transformMat[2]))
    for k in range(4):
        binary.extend(struct.pack('<f', -transformMat[1][k]))




def export_json(context, self, filepath, binfilepath, use_selection, overwrite_default_scenario,
                export_animation):
    version = "1.3"
    binary = os.path.relpath(binfilepath, os.path.commonpath([filepath, binfilepath]))
    global rootFilePath; rootFilePath = os.path.dirname(filepath)

    scn = context.scene
    dataDictionary = collections.OrderedDict()

    if os.path.isfile(filepath):
        file = open(filepath, 'r')
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
        if overwrite_default_scenario or not ('defaultScenario' in oldData):
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
    frame_range = range(scn.frame_start, scn.frame_end + 1) if export_animation else [frame_current]
    
    # Cameras
    cameras = [o for o in bpy.data.objects if o.type == 'CAMERA']

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
                dataDictionary['cameras'][cameraObject.name]['chipHeight'] = camera.sensor_height
                dataDictionary['cameras'][cameraObject.name]['focusDistance'] = camera.dof_distance
                dataDictionary['cameras'][cameraObject.name]['aperture'] = aperture
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
    for i in range(len(lamps)):
        lampObject = lamps[i]
        lamp = lampObject.data
        if lamp.users == 0:
            continue
        if lamp.type == "POINT":
            if lampObject.name not in dataDictionary['lights']:
                dataDictionary['lights'][lampObject.name] = collections.OrderedDict()
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
            directions = []
            radiances = []
            scales = []
            # Go through all frames, accumulate the to-be-exported quantities
            for f in frame_range:
                scn.frame_set(f)
                directions.append(flip_space(lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))))
                radiances.append([lamp.color.r, lamp.color.g, lamp.color.b])
                scales.append(lamp.energy)
            dataDictionary['lights'][lampObject.name]['type'] = "directional"
            dataDictionary['lights'][lampObject.name]['direction'] = junction_path(directions)
            dataDictionary['lights'][lampObject.name]['radiance'] = junction_path(radiances)
            dataDictionary['lights'][lampObject.name]['scale'] = junction_path(scales)
        elif lamp.type == "SPOT":
            if lampObject.name not in dataDictionary['lights']:
                dataDictionary['lights'][lampObject.name] = collections.OrderedDict()
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
                directions.append(flip_space(lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))))
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
    if use_selection:
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
        outputNode = find_material_output_node(material.node_tree)
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
            # TODO: displacement
            if len(outputNode.inputs['Displacement'].links):
                self.report({'WARNING'}, ("Material '%s': displacement output is not supported yet"%(material.name)))
            if len(outputNode.inputs['Volume'].links):
                self.report({'WARNING'}, ("Material '%s': volume output is not supported yet"%(material.name)))
            # TODO: join the material dicts together
            dataDictionary['materials'][material.name] = workDictionary
        except Exception as e:
            self.report({'ERROR'}, ("Material '%s' not converted: %s"%(material.name, str(e))))
            
    # Background
    # Check if the world (aka background) is set
    try:
        if context.scene.world.use_nodes:
            worldOutNode = find_world_output_node(context.scene.world.node_tree)
            if not worldOutNode is None:
                if len(worldOutNode.inputs['Surface'].links) > 0:
                    colorNode = worldOutNode.inputs['Surface'].links[0].from_node
                    if colorNode.bl_idname != 'ShaderNodeTexEnvironment':
                        raise Exception("background lights other than envmaps are not supported yet (node '%s')"%(colorNode.name))
                    if len(colorNode.inputs['Vector'].links) > 0:
                        self.report({'WARNING'}, ("Background: vector input (for e.g. rotation) is not supported yet (node '%s')"%(colorNode.name)))
                    backgroundName = context.scene.name + "_Envmap"
                    dataDictionary['lights'][backgroundName] = collections.OrderedDict()
                    dataDictionary['lights'][backgroundName]['type'] = 'envmap'
                    dataDictionary['lights'][backgroundName]['map'] = colorNode.image.filepath
                    dataDictionary['lights'][backgroundName]['scale'] = 1.0
                    lightNames.append(backgroundName)
    except Exception as e:
        self.report({'ERROR'}, ("Background light did not get set: %s"%(str(e))))

    # Scenarios
    for scene in bpy.data.scenes:
        world = scene.world
        if scene.name not in dataDictionary['scenarios']:
            dataDictionary['scenarios'][scene.name] = collections.OrderedDict()
        cameraName = ''  # type: str
        if hasattr(scene.camera, 'name'):
            if scene.camera.data.type == "PERSP" or scene.camera.data.type == "ORTHO":
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

        # TODO: Envmaps

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
        for obj in objects:
            if is_instance(obj) and (scene not in obj.users_scene or obj.hide_render):
                if obj.name not in dataDictionary['scenarios'][scene.name]['instanceProperties']:
                    dataDictionary['scenarios'][scene.name]['instanceProperties'][obj.name] = collections.OrderedDict()
                dataDictionary['scenarios'][scene.name]['instanceProperties'][obj.name]['mask'] = True


    # CustomJSONEncoder: Custom float formater (default values will be like 0.2799999994039535)
    # and which packs small arrays in one line
    dump = json.dumps(dataDictionary, indent=4, cls=CustomJSONEncoder)
    file = open(filepath, 'w')
    file.write(dump)
    file.close()
    return 0



#   ###   #  #   #     #     ###   #   #
#   #  #  #  ##  #    # #    #  #   # #
#   ###   #  # # #   #   #   ###     #
#   #  #  #  #  ##  #######  #  #    #
#   ###   #  #   #  #     #  #  #    #

def export_binary(context, self, filepath, use_selection, use_deflation, use_compression,
                  triangulate, export_animation):
    scn = context.scene
    # Store current frame to reset it later
    frame_current = scn.frame_current
    frame_range = range(scn.frame_start, scn.frame_end + 1) if export_animation else [frame_current]
    
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

    # Objects Header

    # Type
    binary.extend("Objs".encode())
    instanceSectionStartBinaryPosition = len(binary)  # Save Position in binary has to be corrected later
    # Next section start position
    binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known
    # Global object flags (compression etc.)
    flags = 0x0
    if use_deflation:
        flags |= 1
    if use_compression:
        flags |= 2
    binary.extend(flags.to_bytes(4, byteorder='little'))

    # Get all real geometric objects for export and make them unique (instancing may refer to the same
    # mesh multiple times).
    if use_selection:
        instances = [obj for obj in bpy.context.selected_objects if is_instance(obj)]
    else:
        instances = [obj for obj in bpy.data.objects if is_instance(obj)]
    
    animationObjects = []
    if export_animation:
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
                        create_per_frame_object(scn, instance, animationObjects, frame_range, "_fluidsim_frame_", True)
                    elif fluidMods[0].settings.type != "FLUID":
                        remainingInstances.append(instance)
                elif clothMods:
                    create_per_frame_object(scn, instance, animationObjects, frame_range, "_clothsim_frame_", True)
                else:
                    remainingInstances.append(instance)
                
        instances = remainingInstances
                    

    # Get the number of unique data references. While it hurts to perform an entire set construction
    # there is no much better way. The object count must be known to construct the jump-table properly.
    countOfObjects = len({obj.data for obj in instances}) + len(animationObjects)
    binary.extend(countOfObjects.to_bytes(4, byteorder='little'))

    objectStartBinaryPosition = []  # Save Position in binary to set this correct later
    for i in range(countOfObjects):
        objectStartBinaryPosition.append(len(binary))
        binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known

    print("Exporting objects...")
    activeObject = context.view_layer.objects.active    # Keep this for resetting later
    exportedObjects = OrderedDict()
    for objIdx in range(0, len(instances) + len(animationObjects)):
        isAnimatedObject = objIdx >= len(instances)
        currentObject = animationObjects[objIdx - len(instances)] if isAnimatedObject else instances[objIdx]
        # Due to instancing a mesh might be referenced multiple times
        if currentObject.data in exportedObjects:
            continue
        keyframe = (frame_range[0] + ((objIdx - len(instances)) % len(frame_range))) if isAnimatedObject else 0xFFFFFFFF
        if isAnimatedObject and keyframe == frame_range[0]:
            print(currentObject.name, " (animated, ", len(frame_range), " frames)")
        elif not isAnimatedObject:
            print(currentObject.name)
        idx = len(exportedObjects)
        exportedObjects[currentObject.data] = idx # Store index for the instance export
        write_num(binary, objectStartBinaryPosition[idx], 8, len(binary)) # object start position
        # Type check
        binary.extend("Obj_".encode())
        # Object name
        objectName = currentObject.data.name.encode()
        objectNameLength = len(objectName)
        binary.extend(objectNameLength.to_bytes(4, byteorder='little'))
        binary.extend(objectName)
        # Write the object flags (NOT compression/deflation, but rather isEmissive etc)
        objectFlagsBinaryPosition = len(binary)
        objectFlags = 0
        binary.extend(objectFlags.to_bytes(4, byteorder='little'))
        # keyframe (computed from the animated object index)
        binary.extend(keyframe.to_bytes(4, byteorder='little'))  # TODO keyframes
        # OBJID of previous object in animation
        binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO keyframes
        # Bounding box

        # Calculate Lod chain to get bounding box over all Lods
        lodLevels = []
        lodChainStart = 0
        # TODO: LoD chain
        if len(lodLevels) == 0:
            lodLevels.append(currentObject)  # if no LOD levels the object itself ist the only LOD level

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
        # <Jump Table> LOD

        # Number of entries in table
        binary.extend((len(lodLevels)).to_bytes(4, byteorder='little'))
        lodStartBinaryPosition = len(binary)
        for j in range(len(lodLevels)):
            binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known
        for j in range(len(lodLevels)):
            lodObject = lodLevels[(lodChainStart+j+1) % len(lodLevels)]  # for the correct starting object
            # start Positions
            write_num(binary, lodStartBinaryPosition + j*8, 8, len(binary))
            # Type
            binary.extend("LOD_".encode())
            # Needs to set the target object to active, to be able to apply changes.
            if len(lodObject.users_scene) < 1:
                continue
            context.window.scene = lodObject.users_scene[0] # Choose a valid scene which contains the object
            hidden = lodObject.hide_render
            lodObject.hide_render = False
            context.view_layer.objects.active = lodObject
            mode = lodObject.mode
            bpy.ops.object.mode_set(mode='EDIT')
            if "sphere" not in lodObject:
                mesh = lodObject.to_mesh(preserve_all_data_layers=True)  # applies all modifiers
                bm = bmesh.new()
                bm.from_mesh(mesh)  # bmesh gives a local editable mesh copy
                faces = bm.faces
                faces.ensure_lookup_table()
                facesToTriangulate = []
                for k in range(len(faces)):
                    if len(faces[k].edges) > 4 or (triangulate and len(faces[k].edges) > 3):
                        facesToTriangulate.append(faces[k])
                bmesh.ops.triangulate(bm, faces=facesToTriangulate[:], quad_method='BEAUTY', ngon_method='BEAUTY')
                # Split vertices if vertex has multiple uv coordinates (is used in multiple triangles)
                # or if it is not smooth
                if len(lodObject.data.uv_layers) > 0:
                    # mark seams from uv islands
                    if bpy.ops.uv.seams_from_islands.poll():
                        bpy.ops.uv.seams_from_islands()
                edgesToSplit = [e for e in bm.edges if e.seam or not e.smooth
                    or not all(f.smooth for f in e.link_faces)]
                bmesh.ops.split_edges(bm, edges=edgesToSplit)

                faces = bm.faces  # update faces
                faces.ensure_lookup_table()
                numberOfTriangles = 0
                numberOfQuads = 0
                for k in range(len(faces)):
                    numVertices = len(faces[k].verts)
                    if numVertices == 3:
                        numberOfTriangles += 1
                    elif numVertices == 4:
                        numberOfQuads += 1
                    else:
                        self.report({'ERROR'}, ("%d Vertices in face from object: \"%s\"." % (numVertices, lodObject.name)))
                        return
                bm.to_mesh(mesh)
                bm.free()
                numberOfSpheres = 0
                numberOfVertices = len(mesh.vertices)
                numberOfEdges = len(mesh.edges)
                numberOfVertexAttributes = 0
                numberOfFaceAttributes = 0  # TODO Face Attributes
                numberOfSphereAttributes = 0
            else:  # Change Values if it is an sphere
                numberOfTriangles = 0
                numberOfQuads = 0
                numberOfSpheres = 1
                numberOfVertices = 0
                numberOfEdges = 0
                numberOfVertexAttributes = 0
                numberOfFaceAttributes = 0
                numberOfSphereAttributes = 0  # TODO Sphere Attributes

            # Number of Triangles
            binary.extend(numberOfTriangles.to_bytes(4, byteorder='little'))
            # Number of Quads
            binary.extend(numberOfQuads.to_bytes(4, byteorder='little'))
            # Number of Spheres
            binary.extend(numberOfSpheres.to_bytes(4, byteorder='little'))
            # Number of Vertices
            binary.extend(numberOfVertices.to_bytes(4, byteorder='little'))
            # Number of Edges
            binary.extend(numberOfEdges.to_bytes(4, byteorder='little'))
            # Number of Vertex Attributes
            numberOfVertexAttributesBinaryPosition = len(binary)  # has to be corrected when value is known
            binary.extend(numberOfVertexAttributes.to_bytes(4, byteorder='little'))
            # Number of Face Attributes
            binary.extend(numberOfFaceAttributes.to_bytes(4, byteorder='little'))
            # Number of Sphere Attributes
            binary.extend(numberOfSphereAttributes.to_bytes(4, byteorder='little'))
            if "sphere" not in lodObject:
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
                write_vertex_normals(vertexDataArray, mesh, use_compression)
                for k in range(len(vertices)):
                    vertexDataArray.extend(struct.pack('<2f', uvCoordinates[k][0], uvCoordinates[k][1]))
                vertexOutData = vertexDataArray
                if use_deflation and len(vertexDataArray) > 0:
                    vertexOutData = zlib.compress(vertexDataArray, 8)
                    binary.extend(len(vertexOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(vertexDataArray).to_bytes(4, byteorder='little'))
                binary.extend(vertexOutData)

                # Vertex Attributes
                vertexAttributesDataArray = bytearray()  # Used for deflation
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

                if numberOfVertexAttributes > 0:
                    write_num(binary, numberOfVertexAttributesBinaryPosition, 4, numberOfVertexAttributes)

                    binary.extend("Attr".encode())
                    for uvNumber in range(len(mesh.uv_layers)):
                        if uvNumber == 0:
                            continue
                        uvCoordinates = numpy.empty(len(vertices), dtype=object)
                        uv_layer = mesh.uv_layers[uvNumber]
                        if not uv_layer:
                            continue
                        uvName = uv_layer.name
                        vertexAttributesDataArray.extend(len(uvName.encode()).to_bytes(4, byteorder='little'))
                        vertexAttributesDataArray.extend(uvName.encode())
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((16).to_bytes(4, byteorder='little'))  # Type: 2*f32 (16)
                        vertexAttributesDataArray.extend((len(vertices)*4*2).to_bytes(8, byteorder='little'))  # Count of Bytes len(vertices) * 4(f32) * 2(uvCoordinates per vertex)
                        for polygon in mesh.polygons:
                            for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                                uvCoordinates[mesh.loops[loop_index].vertex_index] = uv_layer.data[loop_index].uv
                        for k in range(len(vertices)):
                            vertexAttributesDataArray.extend(struct.pack('<2f', uvCoordinates[k][0], uvCoordinates[k][1]))
                    for colorNumber in range(len(mesh.vertex_colors)):
                        vertexColor = numpy.empty(len(vertices), dtype=object)
                        vertex_color_layer = mesh.vertex_colors[colorNumber]
                        if not vertex_color_layer:
                            continue
                        colorName = vertex_color_layer.name
                        vertexAttributesDataArray.extend(len(colorName.encode()).to_bytes(4, byteorder='little'))
                        vertexAttributesDataArray.extend(colorName.encode())
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((17).to_bytes(4, byteorder='little'))  # Type: 3*f32 (17)
                        vertexAttributesDataArray.extend((len(vertices)*4*3).to_bytes(8, byteorder='little'))  # Count of Bytes len(vertices) * 4(f32) * 3(color channels per vertex)
                        for polygon in mesh.polygons:
                            for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                                vertexColor[mesh.loops[loop_index].vertex_index] = vertex_color_layer.data[loop_index].color
                        for k in range(len(vertices)):
                            vertexAttributesDataArray.extend(struct.pack('<3f', vertexColor[k][0], vertexColor[k][1], vertexColor[k][2]))

                vertexAttributesOutData = vertexAttributesDataArray
                if use_deflation and len(vertexAttributesDataArray) > 0:
                    vertexAttributesOutData = zlib.compress(vertexAttributesDataArray, 8)
                    binary.extend(len(vertexAttributesOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(vertexAttributesDataArray).to_bytes(4, byteorder='little'))
                binary.extend(vertexAttributesOutData)

                # TODO more Vertex Attributes? (with deflation)
                # Triangles
                triangleDataArray = bytearray()  # Used for deflation
                for polygon in mesh.polygons:
                    if len(polygon.vertices) == 3:
                        for k in range(3):
                            triangleDataArray.extend(polygon.vertices[k].to_bytes(4, byteorder='little'))
                triangleOutData = triangleDataArray
                if use_deflation and len(triangleDataArray) > 0:
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
                if use_deflation and len(quadDataArray) > 0:
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
                if use_deflation and len(matIDDataArray) > 0:
                    matIDOutData = zlib.compress(matIDDataArray, 8)
                    binary.extend(len(matIDOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(matIDDataArray).to_bytes(4, byteorder='little'))
                binary.extend(matIDOutData)
                # Face Attributes
                # TODO Face Attributes (with deflation)
                lodObject.to_mesh_clear()
            else:
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
                        if lodObject.active_material.emit > 0.0:
                            objectFlags |= 1
                            write_num(binary, objectFlagsBinaryPosition, 4, objectFlags)
                else:
                    self.report({'WARNING'}, ("LOD Object: \"%s\" has no materials." % (lodObject.name)))
                    sphereDataArray.extend((0).to_bytes(2, byteorder='little'))  # first material is default when the object has no mats

                sphereOutData = sphereDataArray
                # TODO Sphere Attributes
                if use_deflation and len(sphereDataArray) > 0:
                    sphereOutData = zlib.compress(sphereDataArray, 8)
                    binary.extend(len(sphereOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(sphereDataArray).to_bytes(4, byteorder='little'))
                binary.extend(sphereOutData)
            # reset used mode
            bpy.ops.object.mode_set(mode=mode)
            lodObject.hide_render = hidden
    #reset active object
    context.view_layer.objects.active = activeObject

    # Instances
    write_num(binary, instanceSectionStartBinaryPosition, 8, len(binary))
    # Type
    binary.extend("Inst".encode())

    # Number of Instances
    numberOfInstancesBinaryPosition = len(binary)
    binary.extend((0).to_bytes(4, byteorder='little'))  # has to be corrected later
    numberOfInstances = 0
    print("Exporting animated instances...")
    # Export animated object-instances (different meshes per frame) separately as
    # they don't need per-frame treatment
    for instanceIdx in range(0, len(animationObjects)):
        animatedInstance = animationObjects[instanceIdx]
        index = exportedObjects[animatedInstance.data]
        binary.extend(len(animatedInstance.name.encode()).to_bytes(4, byteorder='little'))
        binary.extend(animatedInstance.name.encode())
        binary.extend(index.to_bytes(4, byteorder='little'))  # Object ID
        keyframe = frame_range[0] + (instanceIdx % len(frame_range))
        binary.extend(keyframe.to_bytes(4, byteorder='little')) # Keyframe
        binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Instance ID
        transformMat = validate_transformation(self, animatedInstance)
        write_instance_transformation(binary, transformMat)
        numberOfInstances += 1
        # Remove the animated object
        bpy.ops.object.select_all(action='DESELECT')
        animatedInstance.select = True
        bpy.ops.object.delete()
        
    print("Exporting regular instances...")
    for currentInstance in instances:
        index = exportedObjects[currentInstance.data]
        transMats = []
        instanceAnimated = False
        for f in frame_range:
            scn.frame_set(f)
            transformMat = validate_transformation(self, currentInstance)
            if not instanceAnimated and len(transMats) > 0 and transMats[0] != transformMat:
                instanceAnimated = True
            transMats.append(transformMat.copy())
        scn.frame_set(frame_current)
        if not instanceAnimated:
            transMats = [transMats[0]]
        for f in range(0, len(transMats)):
            binary.extend(len(currentInstance.name.encode()).to_bytes(4, byteorder='little'))
            binary.extend(currentInstance.name.encode())
            binary.extend(index.to_bytes(4, byteorder='little'))  # Object ID
            keyframe = (frame_range[0] + f) if instanceAnimated else 0xFFFFFFFF
            binary.extend(keyframe.to_bytes(4, byteorder='little')) # Keyframe
            binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Instance ID
            write_instance_transformation(binary, transMats[f])
            numberOfInstances += 1
        
    # Now that we're done we know the amount of instances
    numberOfInstancesBytes = numberOfInstances.to_bytes(4, byteorder='little')
    for i in range(4):
        binary[numberOfInstancesBinaryPosition + i] = numberOfInstancesBytes[i]

    # Reset scene
    context.window.scene = scn
    # Write binary to file
    binFile = open(filepath, 'bw')
    binFile.write(binary)
    binFile.close()
    return 0


def export_mufflon(context, self, filepath, use_selection, use_compression,
                   use_deflation, overwrite_default_scenario, triangulate,
                   export_animation):
    filename = os.path.splitext(filepath)[0]
    binfilepath = filename + ".mff"
    if export_json(context, self, filepath, binfilepath, use_selection,
                   overwrite_default_scenario, export_animation) == 0:
        print("Succeeded exporting JSON")
        if export_binary(context, self, binfilepath, use_selection,
                         use_compression, use_deflation, triangulate,
                         export_animation) == 0:
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
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator


class MufflonExporter(Operator, ExportHelper):
    """This appears in the tooltip of the operator and in the generated docs"""
    bl_idname = "mufflon.exporter"  # important since its how bpy.ops.import_test.some_data is constructed
    bl_label = "Export Mufflon Scene"

    # ExportHelper mixin class uses this
    filename_ext = ".json"
    filter_glob = StringProperty(
            default="*.json;*.mff",
            options={'HIDDEN'},
            maxlen=255,  # Max internal buffer length, longer would be clamped.
            )

    # List of operator properties, the attributes will be assigned
    # to the class instance from the operator settings before calling.
    use_selection = BoolProperty(
            name="Selection Only",
            description="Export selected objects only",
            default=False,
            )
    use_compression = BoolProperty(
            name="Compress normals",
            description="Apply compression to vertex normals (Octahedral)",
            default=False,
            )
    use_deflation = BoolProperty(
            name="Deflate",
            description="Use deflation to reduce file size",
            default=False,
            )
    triangulate = BoolProperty(
            name="Triangulate",
            description="Triangulates all exported objects",
            default=False,
            )
    overwrite_default_scenario = BoolProperty(
            name="Overwrite default scenario",
            description="Overwrite the default scenario when exporting JSON if already set",
            default=True,
            )
    export_animation = BoolProperty(
            name="Export animation",
            description="Exports instance transformations per animation frame",
            default=False,
            )
    path_mode = path_reference_mode

    def execute(self, context):
        return export_mufflon(context, self, self.filepath, self.use_selection,
                              self.use_deflation, self.use_compression,
                              self.overwrite_default_scenario, self.triangulate,
                              self.export_animation)


# Only needed if you want to add into a dynamic menu
def menu_func_export(self, context):
    self.layout.operator(MufflonExporter.bl_idname, text="Mufflon (.json/.mff)")


def register():
    bpy.utils.register_class(MufflonExporter)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.utils.unregister_class(MufflonExporter)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)


if __name__ == "__main__":
    register()
