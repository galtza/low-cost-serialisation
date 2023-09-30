# Low-Cost Serialization
Your mandate is to **do telemetry** of the content of objects in memory. It is essentially **memcpy-ing** objects from their original memory locations to a stream buffer. It is for debugging reasons. These objects are polymorphic and accessed via pointers. But, you hit a wall. You do not know the size of the actual object. **RTTI**, though useful, doesn’t deliver. Hence, we need a new approach.

This novel C++ debugging technique is that approach. It pairs **vftable** pointers with class sizes. Thus, it quickly deduces the memory footprint of a polymorphic pointer instance. Once the right size is known, it serialises the class memory straight into bits. A separate tool with **PDB** details ensures this saved memory is accurately interpreted.

In essence, this technique presents a refined method to serialise C++ polymorphic object content **without** the need for **code instrumentation**.

### Our Goal
Simply put, our goal is both to **serialise objects** and to employ **reflection**, all while causing **minimal interference** in the host application.

To access the raw data of an object, we need its **address** and **size**. Getting the address is straightforward, getting the size is not.

### The problem

The crux of our problem is deducing the size of polymorphic objects.

For variables that are **not pointers** nor references, we use the `sizeof` operator:

```
int x = 42; // size of `x` is sizeof(int), using type
my_class y; // size of `y` is sizeof(y), using expr
```

For **non-polymorphic** object pointers, the size is the `sizeof` of the pointed object type:

```
struct A {};
struct B : A {};
int main () {
  A a;
  B b;
  A* x = &a; // [ok]  size of `*x` is sizeof(A)
  A* y = &b; // [bad] size of `*y` is still sizeof(A)
  B* z = &b; // [ok]  size of `*z` is sizeof(B)
}
```

Notice that the usage of variable `y` is technically valid in C++, but it's somewhat of a misuse in this context: with `y` we cannot access anything specific to `b` even though the underlying object is indeed a `b`. In this particular case, we are left with no choice but to deduce the size as `sizeof(A)`.

Now, for **polymorphic object pointers**, the size cannot be determined at compile time either:

```
struct A { virtual void foo () {} };
struct B : A {};
void main () {
  A a;
  B b;
  A* x = &a; // Size of `*x` is `sizeof(A)`
  A* y = &b; // Size of `*y` is `sizeof(B)` (Cannot deduce at compile time)
}
```

This requires us to use "_the technique_".

### The Technique

At runtime, polymorphic objects embed a pointer to their class **unique** **vftable**. While the pointer identifies the **vftable**, it does not reveal the actual class nor its size. However, with the help of **PDB** files, we can correlate the **vftable** address to the size of the corresponding class prior to execution.

The **initial step** then is to establish a link between each **vftable** address and the size of the associated class. Alongside this, we generate the data required for interpreting a deep copy of an object, this is, the class memory layout.

At runtime, the **host application** reads this precomputed data and utilises it to deduce the size of the object we want to make a full copy of.

The binary blob is then sent to a **viewer application** where it also uses the preprocessed data as a reference for interpreting the blob. Although its primary function is to display this information, it may also allow to modify data. In that case, the modification is sent over to the host application for thread-safe deployment.

Enhancing this technique could potentially improve **RTTI** capabilities as well. However, it's advisable to employ this mainly in non-release versions due to considerations of performance and security.

### *vftable* location

As we mentioned earlier, each polymorphic object in memory has **embedded** a pointer to its class's **vftable**. While this is commonly placed at the object's start, it's not a guarantee mandated by the C++ standard. Nevertheless, we can reasonably presume that the **vftable** is either at the object's beginning or its end. To pinpoint its precise location, consider the following compile-time technique:

```c++
namespace qcstudio::cpp {
    struct find_vtable_t {
        virtual ~find_vtable_t() {}
        uint64_t unused;
    };
    constexpr auto is_vtable_at_beginning = offsetof(find_vtable_t, unused) != 0;
}
```

By employing this code, we can determine if the **vftable** is situated at the start of our object.

### **The Pre-processing**

The "*baking*" phase is a critical component of our solution. Utilising **LLVM** tools, and specific libraries like `LLVMDebugInfoPDB.lib`, we streamline this process. While Microsoft's **DIA SDK** can offer similar information, the **LLVM** toolkit is generally more efficient. 

##### Glossary

> **RVA** -> **R**elative **V**irtual **A**ddress
>
> **VA** -> **V**irtual **A**ddress
>
> **TI** -> **T**ype **I**ndex on the global type table per module

##### Gathering PDB Files

Firstly, ensure that all relevant **PDB** files are at hand. These could be either your own, third-party, or even system modules. These files are generated during the build stage. 

##### Extracting Key Information

For each class, we target the following essential details: 

1. **RVA** of the **vftable** 
2. Class **size** 
3. **Type Index (TI)** 
4. Memory **layout**

##### Acquiring vftable RVA

Run `llvm-pdbutil.exe dump --globals example.pdb` to compile a comprehensive list of global symbols. You'll want to filter these by `S_GDATA32` and only keep the ones ending the **vftable** mark.  

For instance:

```
  53090912 | S_GDATA32 [size = 32] `AActor::`vftable'` 
           type = 0xBE66 (), addr = 0003:5699384
```

>  Note: the `addr` format is `section:offset`

##### Converting to RVA

To transform these addresses to **RVA**, execute `llvm-pdbutil.exe dump --section-headers example.pdb`. From this, you extract the virtual address of the particular section you are interested in and apply the formula `rva = section_rva + offset`.

```
...
  SECTION HEADER #3
    .rdata name
   33D72D6 virtual size
   9154000 virtual address
   33D7400 size of raw data
   9152800 file pointer to raw data
         0 file pointer to relocation table
         0 file pointer to line numbers
         0 number of relocations
         0 number of line numbers
  40000040 flags
           IMAGE_SCN_CNT_INITIALIZED_DATA
           IMAGE_SCN_MEM_READ
...
```

I our `AActor` case:

```
rva = 0x9154000 + 5699384 = 0x96C3738
```

##### Discovering Class Size and Type Index

To find the class size and type index, run `llvm-pdbutil.exe dump --types example.pdb`. Here, avoid forward references like `0x1DA3` in the example and look for entries with a `sizeof` attribute.  

```
...
     0x1DA3 | LF_CLASS [size = 44] `AActor`
              unique name: `.?AVAActor@@`
              vtable: <no type>, base list: <no type>, field list: <no type>
              options: forward ref (-> 0x349B9) | has unique name, sizeof 0
...
    0x349B9 | LF_CLASS [size = 44] `AActor`
              unique name: `.?AVAActor@@`
              vtable: 0x5CBF, base list: <no type>, field list: 0x349B8
              options: has ctor / dtor | contains nested class | has unique name | overloaded operator | overloaded operator=, sizeof 1056
...
```

In this particular case, the entry for the `AActor` is `0x96C3738 -> 1056, 0x349B9`.

##### The memory Layout

The `field list` provides us with the necessary insights to reconstruct the memory layout. This, in essence, allows us to interpret the bit blob in a manner similar to a debugger. 

Our `AActor` field list after filtering non-relevant symbols is:

```
0x349B8 | LF_FIELDLIST [size = 28420]
  - LF_MEMBER [name = `NetPushId_Internal`, Type = 0x0023 (unsigned __int64), offset = 48, attrs = private]
  - LF_MEMBER [name = `PrimaryActorTick`, Type = 0x23834, offset = 56, attrs = public]
  - LF_MEMBER [name = `bNetTemporary`, Type = 0x2343, offset = 104, attrs = public]
  - LF_MEMBER [name = `bNetStartup`, Type = 0x2347, offset = 104, attrs = public]
  - LF_MEMBER [name = `bOnlyRelevantToOwner`, Type = 0x234C, offset = 104, attrs = public]

```

We have access to all the types so we can reconstruct the bit blob as good as a debugger would do.

##### Summary

In conclusion, the baking process yields a small file for each **PDB** that encapsulates the required information to serialize and reinterpret bit blobs efficiently.

### **Host Application**

The host application tracks details such as base address, path, and size of loaded modules. Also, it loads the **vftable**-related preprocessed data for the module. However, it must translate the `RVA` in the table to `VA` using the formula `VA = RVA - module base addr`.

For serialisation of polymorphic pointers, the **vftable** pointer is fetched, which instantly gives us the object size for the byte blob. 

The C++ code snippet below demonstrates how to deduce the size of the data you're trying to serialise. Note that the function relies on a predefined map, `g_vt_map`, to fetch sizes of polymorphic types.

```cpp
map<uintptr_t, pair<size_t, uint32_t>> g_vt_map; // vftable rva -> (type size, type index)

template<typename T>
auto get_size_and_index(const T& _input, uintptr_t _base) -> pair<size_t, uint32_t> {
    if constexpr (!is_pointer_v<T>) {
        return {sizeof(T), 0xffffffff};
    } else {
        using U = remove_pointer_t<T>;
        if constexpr (!is_polymorphic_v<U>) {
            return {sizeof(U), 0xffffffff};
        } else {
            auto vt_rva = *reinterpret_cast<const uintptr_t*>(_input) - _base;
            if (auto it = g_vt_map.find(vt_rva); it != g_vt_map.end()) {
                return {it->second.first, it->second.second};
            } else {
                return {sizeof(U), 0xffffffff};
            }
        }
    }
}
```

Lastly, the type index is included in the data sent for correct interpretation at the receiver's end.

### **How to Interpret Data**

When byte blobs arrive from the host application, decoding and presenting to the user becomes straightforward due to the comprehensive class structure descriptions available from the preprocessed data, including type hierarchies, member variables, and size information.

Specifically, it utilises the type index sent along with the byte blob and looks up the corresponding type in the preprocessed data, followed by mapping the byte blob back to the object fields.

Finally, the viewer application can allow to modify data. In that case we would need to send back to the host application the memory blocks to substitute at a convenient and safe moment in the host application.

### Some Limitations
  1. Incompatibility with CRTP

_**CRTP**_ (Curiously Recurring Template Pattern) is a compile-time technique for achieving polymorphism. Since our method relies on the _**vftable**_ of dynamically polymorphic classes, it is not compatible with _**CRTP**_.

  2. Dependency on **External Processing**

While external processing is not ideal, the adequate toolset can simplify this aspect considerably.

  3. Limitations with **Dynamic Memory Allocation**

We cannot deduce the structure of data types that rely on dynamic memory allocation, such as containers. For example, if a class contains a field of type _**std::vector**_, the fields controlling access to storage likely reside within the object's memory space, but the actual storage does not due to its dynamic nature.

Nevertheless, we can sort out this issue to some extent. For example, _**std::vector**_ objects guarantee linear memory. This is not the case for associative containers like _**std::map**_, where the internal representation is inherently more complex.

### Conclusions

This approach significantly enhances debugging capabilities, demanding only modest adjustments to your build workflow and minimal code intrusion in the host application.

In a straightforward three-step process, you're all set:

1. Utilise the **external utility** to preprocess data from **PDB**s.
2. Within the **host application**, import preprocessed data and use it to serialise.
3. Examine the serialised data using an **external viewer**.

Undoubtedly, this method offers a substantial leap in application comprehension, requiring comparatively minimal coding effort.