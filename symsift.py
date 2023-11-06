# -*- coding: utf-8 -*-

# Python libs

import subprocess
import os
import sys
import re
import tempfile
import time
import contextlib
import shutil
from traceback import print_exc
from multiprocessing import Pool

# Custom libs

from pdb_utils import BasicTypes # TODO: Use this!
from dual_output import DualOutput

'''
    Define all the regexp we will be using in this file
'''

g_type_header_regexp = re.compile(r'^\s*(0x[\da-fA-F]+)\s*\|\s*(LF_\w+)', re.DOTALL)                # Header of each type
g_fwd_ref_regexp     = re.compile(r'forward ref \(-> ([0-9-A-Fx]+)\)', re.DOTALL)                   # Detect fwd declarations in types (only interested in the actual type)
g_scoped_regexp      = re.compile(r'\boptions:.*\bscoped\b', re.DOTALL)                             # Detect a scoped type
g_class_name_regexp  = re.compile(r'LF_.*\s+\[size\s*=\s*[0-9]+\s*]\s+`(.+?)`\n', re.DOTALL)        # Get the type class name
g_field_list_regexp  = re.compile(r'field list: ([0-9-A-Fx]+)', re.DOTALL)                          # Detect the field list of the class
g_unique_name_regexp = re.compile(r'unique name:\s`(.+?)`\n', re.DOTALL)                            # Get the unique type name
g_sizeof_regexp      = re.compile(r'sizeof ([0-9]+)', re.DOTALL)                                    # Get the type sizeof
g_GDATA32_regexp     = re.compile(r'(\d+)\s+\|\s+S_GDATA32\s+\[.*\]\s+`(.+)(?:::`vftable\')')       # Parse the GDATA32
g_addr_regexp        = re.compile(r'addr\s+=\s+(\d+:\d+)')                                          # Get the address (section:offset)
g_debug              = True
g_wanted_types = [
    "LF_CLASS",
    "LF_STRUCTURE",
    "LF_FIELDLIST",
]

def get_filename(_file_path):
    return os.path.splitext(os.path.basename(_file_path))[0]

def worker(_task):
    '''
        Worker that calls llvm-pdbutils
    '''
    pdb_name, options = _task
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.txt')
    command = ["llvm-pdbutil.exe"] + options.split() + [pdb_name]
    with open(temp_file.name, 'w', encoding='utf-8') as outfile:
        subprocess.run(command, stdout=outfile)
    return temp_file.name

def invoke_llvm(_pdb_name):
    '''
        Invoke all the llvm tools we need
    '''
    options_list = ["dump --globals", "dump --symbols", "dump --types", "dump --section-headers"]
    tasks = [(_pdb_name, options) for options in options_list]
    with Pool() as pool:
        generated_files = pool.map(worker, tasks)

    return generated_files

def get_types_with_vftable(_in_globals_file_path, _in_section2rva):
    '''
        
    '''
    out_types = {}
    max_width = -1
    with open(_in_globals_file_path, 'r', encoding='utf-8') as file_object:
        line1 = ""
        for line2 in file_object:
            m1 = g_GDATA32_regexp.match(line1)
            if m1:
                m2 = g_addr_regexp.search(line2.strip())
                if m2:
                    type_name, addr = m1.group(2), m2.group(1)
                    section, offset = [ int(x) for x in addr.split(":") ]
                    section_rva = _in_section2rva[section]
                    out_types[type_name] = [ section_rva + offset ]
                    if len(type_name) > max_width:
                        max_width = len(type_name)

            line1 = line2.strip()

    return out_types, max_width

def generate_section2rva(file_path):
    """
    Parses a given sections file to extract section numbers and their corresponding virtual addresses.
    
    Parameters:
    file_path: str - Path to the sections part file
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
    
    # Regular expression to capture the section header number and the virtual address
    pattern = re.compile(r'SECTION HEADER #(\d+).*?([A-Fa-f0-9]+) virtual address', re.S)
    
    section_data = {}
    for match in pattern.findall(file_content):
        section_number, virtual_address = match
        section_data[int(section_number)] = int(virtual_address, 16)

    return section_data

def process_class(_content, _type_index, _fwd, _type_positions, _inout_types):

    # Is this a forward declaration?

    m = g_fwd_ref_regexp.search(_content)
    if m:
        fwd_type = int(m.group(1), 16)
        _fwd[_type_index] = fwd_type
        return

    # Parse the actual class/struct data (field list and the sizeof)

    '''
    0x10DB | LF_STRUCTURE [size = 132] `TRegistrationInfo<UEnum,FEnumReloadVersionInfo>`
             unique name: `.?AU?$TRegistrationInfo@VUEnum@@UFEnumReloadVersionInfo@@@@`
             vtable: <no type>, base list: <no type>, field list: 0x10DA
             options: has ctor / dtor | contains nested class | has unique name, sizeof 24
    '''
    m = g_class_name_regexp.search(_content)
    if m:
        type_name = m.group(1)

        # Don't want scoped types

        must_delete = g_scoped_regexp.search(_content) is not None
        if must_delete:
            if type_name in _inout_types:
                del _inout_types[type_name]
            return

        # No unammed types

        if "<unnamed-" in type_name:
            return

        m = g_unique_name_regexp.search(_content)
        if m:
            unique_type_name = m.group(1)        
            m = g_field_list_regexp.search(_content)
            if m:
                field_list_index = int(m.group(1), 16)
                m = g_sizeof_regexp.search(_content)
                sizeof = int(m.group(1), 10)
                rva = 0
                if type_name in _inout_types:
                    elem = _inout_types[type_name]
                    rva = elem[0]
                    assert (len(elem) == 1 or elem[-1] == sizeof)
                    if False and g_debug and len(elem) > 1:
                        print("name `%s` repeated\n    old: rva(0x%X); type_index(0x%-8X); fields_index(0x%-8X), sizeof(%d), unique_name(%s)\n    new: rva(0x%X); type_index(0x%-8X); fields_index(0x%-8X), sizeof(%d), unique_name(%s)" % (
                            type_name, 
                            elem[0], elem[1],     elem[2],          elem[4], elem[3], 
                            elem[0], _type_index, field_list_index, sizeof,  unique_type_name 
                        ))
                _inout_types[type_name] = [rva, _type_index, field_list_index, unique_type_name, sizeof]

def process_fieldlist(_content, _type_index, _fwd, _type_positions, _inout_types):
    pass

def process_current_type(_content, _fwd, _type_positions, _inout_types, _pos):

    m = g_type_header_regexp.search(_content)
    if not m or len(m.groups()) != 2:
        return        

    # extrat the type and name

    type_idx, leaf_type_name = m.groups()
    type_idx = int(type_idx, 16)

    # store for future usage if it is interesting for other future parsing cases

    if leaf_type_name in g_wanted_types:
        _type_positions[type_idx] = _pos

    # process the actual type

    if leaf_type_name == "LF_CLASS" or leaf_type_name == "LF_STRUCTURE":
        process_class(_content, type_idx, _fwd, _type_positions, _inout_types)
    elif leaf_type_name == "LF_FIELDLIST":
        process_fieldlist(_content, type_idx, _fwd, _type_positions, _inout_types)

def fill_types_info(_in_types_file, _inout_types):
    '''
        Get information from the types (sizeof, layout, etc.)
        See: https://github.com/microsoft/microsoft-pdb/blob/805655a28bd8198004be2ac27e6e0290121a5e89/include/cvinfo.h#L772
    '''

    type_positions = {} # type_index -> file pos
    fwrd           = {} # type_index -> type_index
    content        = "" # a type content (included all lines)
    content_pos    = -1

    # Pass 1: Collect LF_CLASS matching vftable names

    with open(_in_types_file, 'r', encoding='utf-8') as f:

        pos = f.tell()
        line = f.readline()

        while line:

            try:
                m = g_type_header_regexp.search(line)
                if m:
                    if len(m.groups()) == 2:
                        type_idx, leaf_type_name = m.groups()
                        if content:
                            process_current_type(content, fwrd, type_positions, _inout_types, content_pos)
                            content = line
                            content_pos = pos

                else:
                    content += line

            except:
                print_exc ()
            finally:
                pos = f.tell()
                line = f.readline()


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("Usage: python symsift.py <input_pdb_file>")
        sys.exit(1)

    '''
        Get the following info: 
        ● global symbols
        ● all symbols
        ● types
        ● sections
    '''

    pdb_name = get_filename(sys.argv[1])
    globals_file, symbols_file, types_file, sections_file = invoke_llvm(sys.argv[1])
    if g_debug:
        subprocess.Popen(['gvim.exe', '-p', globals_file, symbols_file, types_file, sections_file], shell=True)

    '''
        Get the RVA of each section (`.text`, `.rdata`, `.bss`)
    '''

    section2rva = generate_section2rva(sections_file)

    '''
        Get the types with vftable (name -> RVA). 
        Note: Later we will gather more information!
    '''

    types_with_vftable, max_name_width = get_types_with_vftable(globals_file, section2rva)

    '''
        Gather more information about those types:
        ● the sizeof (for host app memcpy-ing)
        ● 
    '''

    fill_types_info(types_file, types_with_vftable)

    '''
        Generate the binaries for these types
    '''    

    # TODO...

    # Generate the binary file for the VIEWER to know how to interpret the layout of the memory received by telemetry

    # TODO...

    detailed_types = {}
    virtual_detailed_types = {}
    unexpanded_types = {}

    for type_name, details in types_with_vftable.items():
        if len(details) > 1:
            if details[0] > 0:
                virtual_detailed_types[type_name] = details
            else:
                detailed_types[type_name] = details
        elif len(details) == 1:
            unexpanded_types[type_name] = details

    # Debug

    if g_debug:
        if not os.path.exists(".out"):
            os.makedirs(".out")

        with DualOutput('.out/%s.output.txt' % pdb_name) as output:
            with contextlib.redirect_stdout(output):

                print ("== virtual types ======")

                for type_name, (vftable_rva, type_index, field_index, unique_type_name, sizeof) in sorted(virtual_detailed_types.items(), key=lambda item: item[1][-1]):
                    print(f"Sizeof: {str(sizeof).rjust(6)} | type: {hex(type_index).rjust(8)} | fields: {hex(field_index).rjust(8)} | vftable rva: {hex(vftable_rva).ljust(8)} | name: {type_name.ljust(max_name_width)}")

                print ("\n\n== other types ======")

                for type_name, (vftable_rva, type_index, field_index, unique_type_name, sizeof) in sorted(detailed_types.items(), key=lambda item: item[1][-1]):
                    print(f"Sizeof: {str(sizeof).rjust(6)} | type: {hex(type_index).rjust(8)} | fields: {hex(field_index).rjust(8)} | vftable rva: {hex(vftable_rva).ljust(8)} | name: {type_name.ljust(max_name_width)}")

                print ("\n\n== Unexpanded virtuals ======")

                for type_name, (vftable_rva, ) in sorted(unexpanded_types.items(), key=lambda item: item[1]):
                    print(f"vftable rva: {hex(vftable_rva).ljust(8)} | name: {type_name.ljust(max_name_width)}")

        # Clean up

        for (original_path, new_name) in [(globals_file, ".out/%s.globals.txt"%pdb_name), (symbols_file, ".out/%s.symbols.txt"%pdb_name), (types_file, ".out/%s.types.txt"%pdb_name), (sections_file, ".out/%s.sections.txt"%pdb_name)]:
            shutil.move (original_path, new_name)
    else:
        for temp_file in [globals_file, symbols_file, types_file, sections_file]:
            os.remove(temp_file)
